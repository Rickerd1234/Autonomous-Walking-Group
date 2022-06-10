from itertools import chain
import time

import depthai as dai
import cv2
import numpy as np

import matplotlib.pyplot as plt
import seaborn as sns

from SleeveHandler import SleeveHandler


############################## Settings ##############################

# Camera Settings
USB_2_MODE = True

# Output settings
leftHanded = False
visualize = True
plotting = False
snapshot = False

# Grid settings
rows = 5
columns = 8
# Horizontal grouping
h_left_group = [0,1,2]
h_center_group = [3,4]
h_right_group = [5,6,7]
# Vertical grouping
v_top_group = [0,1]
v_center_group = [2]
v_bottom_group = [3,4]

# Model settings
binsize = 250
SOFT_TRESHOLD       = 10
MEDIUM_TRESHOLD     = 5
INTENSE_TRESHOLD    = 2

# Define the measure used by the model
def measure(block):
    if len(block) == 0: return -1

    vals, counts = np.unique(block//binsize, return_counts=True)
    return vals[np.argmax(counts)]


############################## Constants ##############################

resolution = (640, 400) # Used to properly create grid, does not influence camera
white = (255, 255, 255)
green = (0, 255, 0)
yellow = (0, 204, 255)
red = (0, 0, 255)

colormap = {
    SleeveHandler.OFF:     white,
    SleeveHandler.SOFT:    green,
    SleeveHandler.MEDIUM:  yellow,
    SleeveHandler.INTENSE: red
}

############################## Helper Functions ##############################

# Define helper functions
def blockshaped(arr, nrows, ncols):
    """
    This function was obtained from stack-overflow:
    https://stackoverflow.com/a/16858283

    Return an array of shape (n, nrows, ncols) where
    n * nrows * ncols = arr.size

    If arr is a 2D array, the returned array should look like n subblocks with
    each subblock preserving the "physical" layout of arr.
    """
    h, w = arr.shape
    assert h % nrows == 0, f"{h} rows is not evenly divisible by {nrows}"
    assert w % ncols == 0, f"{w} cols is not evenly divisible by {ncols}"
    return (arr.reshape(h//nrows, nrows, -1, ncols)
               .swapaxes(1,2)
               .reshape(-1, nrows, ncols))

# Function to convert measure into output signal
def setGridSignals(value):
    if   (value < 0 or value > SOFT_TRESHOLD): return SleeveHandler.OFF
    elif (value < INTENSE_TRESHOLD): return SleeveHandler.INTENSE
    elif (value < MEDIUM_TRESHOLD):  return SleeveHandler.MEDIUM
    else:                            return SleeveHandler.SOFT

# Function to convert danger_levels into a single output command
def getOutputSignal(danger_levels):
    intensity = max(danger_levels)

    left = h_center = right = bottom = v_center = top = False
    for i, l in enumerate(danger_levels):
        if l > SleeveHandler.OFF:
            row = i // columns
            column = i % columns
            
            if row in v_top_group:
                top = True
            elif row in v_center_group:
                v_center = True
            elif row in v_bottom_group:
                bottom = True            
            
            if column in h_left_group:
                left = True
            elif column in h_center_group:
                h_center = True
            elif column in h_right_group:
                right = True

    base = SleeveHandler.BASE_COMMAND + SleeveHandler.TAP #+ SleeveHandler.STROKE_SLOW
    command = base

    if (left and right) or h_center:
        command += SleeveHandler.H_CENTER
    elif left:
        command += SleeveHandler.LEFT
    elif right:
        command += SleeveHandler.RIGHT

    if (top and bottom) or v_center:
        command += SleeveHandler.V_CENTER
    elif top:
        command += SleeveHandler.TOP
    elif bottom:
        command += SleeveHandler.BOTTOM #+ SleeveHandler.INVERT_VERTICAL

    if intensity == SleeveHandler.SOFT:
        command += SleeveHandler.DECREASE + "10"
    elif intensity == SleeveHandler.MEDIUM:
        command += SleeveHandler.DECREASE + "5"
    elif intensity == SleeveHandler.INTENSE:
        command += SleeveHandler.DECREASE + "0"
    elif intensity == SleeveHandler.OFF:
        command = ""

    return command, intensity

############################## Camera Pipelines & Settings ##############################

# Setup camera pipelines
pipeline = dai.Pipeline()
left = pipeline.create(dai.node.MonoCamera)
left.setBoardSocket(dai.CameraBoardSocket.LEFT)
left.setResolution(dai.MonoCameraProperties.SensorResolution.THE_400_P)
right = pipeline.create(dai.node.MonoCamera)
right.setResolution(dai.MonoCameraProperties.SensorResolution.THE_400_P)
right.setBoardSocket(dai.CameraBoardSocket.RIGHT)

# Setup stereodepth pipeline (with left and right camera as input)
stereo = pipeline.create(dai.node.StereoDepth)
left.out.link(stereo.left)
right.out.link(stereo.right)

# Set settings to get a better depth image
stereo.setLeftRightCheck(True)
stereo.setExtendedDisparity(False)
stereo.setSubpixel(False)
stereo.setDefaultProfilePreset(dai.node.StereoDepth.PresetMode.HIGH_ACCURACY)
stereo.initialConfig.setMedianFilter(dai.MedianFilter.KERNEL_7x7)

# Configure stereo pipeline
config = stereo.initialConfig.get()
config.postProcessing.speckleFilter.enable = False
config.postProcessing.speckleFilter.speckleRange = 50
config.postProcessing.temporalFilter.enable = True
config.postProcessing.spatialFilter.enable = True
config.postProcessing.spatialFilter.holeFillingRadius = 2
config.postProcessing.spatialFilter.numIterations = 1
config.postProcessing.thresholdFilter.minRange = 400
config.postProcessing.thresholdFilter.maxRange = 15000
config.postProcessing.decimationFilter.decimationFactor = 1
stereo.initialConfig.set(config)

# Setup output pipeline (with stereodepth as input)
depthOut = pipeline.create(dai.node.XLinkOut)
depthOut.setStreamName("depth")
stereo.depth.link(depthOut.input)


############################## Setting Processing ##############################

# Process the settings to create a grid
w, h = int(resolution[0]/columns), int(resolution[1]/rows)
grid = [((c*w, r*h), ((c+1)*w, (r+1)*h)) for r in range(rows) for c in range(columns)]

# Process the settings to create subplots
fig, axes = plt.subplots(nrows=rows, ncols=columns, sharey=True, sharex=True)
axes = list(chain.from_iterable(axes))

# Create the window to display depth frame
if visualize: cv2.namedWindow("depth")


############################## Initialize SleeveHandler ##############################

sleeveHandler = SleeveHandler()
sleeveHandler.setLeftHandMode(leftHanded)


############################## Running the Model ##############################

# Initialize the device and pipelines
with dai.Device(pipeline, usb2Mode=USB_2_MODE) as device:
    # Define queue to retrieve frames from
    depthQueue = device.getOutputQueue(name="depth", maxSize=4, blocking=False)

    # Initialize variable for frame counter
    frame_count = 0
    start_time = time.monotonic()
    

    while True:
        # Get new frame
        depth = depthQueue.get()
        depthFrame = np.array(depth.getFrame())

        # Store a snapshot of the data after 100 frames
        if snapshot and frame_count >= 100:
            with open("stored_depthFrame.bin", "wb") as file:
                np.save(file, depthFrame, allow_pickle=True)
            break


        # Process the data into blocks
        blocks = blockshaped(depthFrame, h, w)
        # Drop all zeroes from data
        blocks = np.array(list(map(lambda block: block[block > 0], blocks)), dtype=object)
        # Process the blocks into singular danger values (per block)
        values = list(map(measure, blocks))

        danger_levels = list(map(setGridSignals, values))
        command, intensity = getOutputSignal(danger_levels)
        if command != "":
            sleeveHandler.processSignal(command, intensity)


        if visualize:
            # Process the frame to be shown
            depthFrameColor = cv2.normalize(depthFrame, None, 255, 0, cv2.NORM_INF, cv2.CV_8UC1)
            depthFrameColor = cv2.equalizeHist(depthFrameColor)
            depthFrameColor = cv2.applyColorMap(depthFrameColor, cv2.COLORMAP_OCEAN)

            # Display the grid layout and information
            for i, (pos, size) in enumerate(grid):
                color = colormap[danger_levels[i]]
                cv2.putText(depthFrameColor, str(values[i]), (pos[0]+5, size[1]-5), cv2.FONT_HERSHEY_DUPLEX, 1, color)
                cv2.rectangle(depthFrameColor, pos, size, color, cv2.FONT_HERSHEY_DUPLEX)

            # Display the depthframe
            cv2.imshow("depth", depthFrameColor)

            # Wait for 'q' keypress on the depth frame window to close
            if cv2.waitKey(1) == ord('q'):
                break


        if plotting:
            # Plot the density graph per box
            for i, block in enumerate(blocks):
                sns.kdeplot(np.array(block), ax=axes[i])

            # Wait 1 second before plotting new values
            plt.pause(1)


        # Update frame counter
        frame_count += 1

        # Print frame rate
        current_time = time.monotonic()
        if (current_time - start_time) > 1 and (frame_count % 10 == 0):
            print("FPS: {:.2f}".format(10 / (current_time - start_time)), end="\r")
            start_time = current_time