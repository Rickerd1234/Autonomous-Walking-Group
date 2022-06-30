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
LEFT_HANDED = False
VISUALIZE_MODEL = True
FULL_SCREEN_MODE = True
SHOW_GRID = False
PLOT_DATA = False
CREATE_SNAPSHOT = False
# Arrow settings
SHOW_ARROW = True
ARROW_LENGTH = 100

# Grid settings
GRID_ROWS = 5
GRID_COLUMNS = 8
# Horizontal grouping
H_LEFT_GROUP = [0,1,2]
H_CENTER_GROUP = [3,4]
H_RIGHT_GROUP = [5,6,7]
# Vertical grouping
V_TOP_GROUP = [0,1]
V_CENTER_GROUP = [2]
V_BOTTOM_GROUP = [3,4]

# Model settings
BIN_SIZE = 125
SOFT_TRESHOLD       = 20
MEDIUM_TRESHOLD     = 10
INTENSE_TRESHOLD    = 6

# Define the measure used by the model
def measure(block):
    if len(block) == 0: return -1

    # Aggregate the values in bins
    vals, counts = np.unique(block//BIN_SIZE, return_counts=True)
    # Return the value of the largest bin (containing most data)
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
def getOutputSignal(danger_levels, center_point):
    intensity = max(danger_levels)

    # Aggregate the cell values into predefined regions
    h_sum, h_count = [0 for _ in range(3)], [0 for _ in range(3)]
    v_sum, v_count = [0 for _ in range(3)], [0 for _ in range(3)]
    for i, level in enumerate(danger_levels):
        if level > SleeveHandler.OFF:
            row = i // GRID_COLUMNS
            column = i % GRID_COLUMNS
            
            if row in V_TOP_GROUP:
                v_sum[0] += level
                v_count[0] += 1
            elif row in V_CENTER_GROUP:
                v_sum[1] += level
                v_count[1] += 1
            elif row in V_BOTTOM_GROUP:
                v_sum[2] += level
                v_count[2] += 1            
            
            if column in H_LEFT_GROUP:
                h_sum[0] += level
                h_count[0] += 1
            elif column in H_CENTER_GROUP:
                h_sum[1] += level
                h_count[1] += 1
            elif column in H_RIGHT_GROUP:
                h_sum[2] += level
                h_count[2] += 1

    # Get the mean value of each horizontal and vertical region
    convertToMeans = lambda sums, counts: [s/c if c > 0 else 0 for s,c in zip(sums, counts)]
    h_means = convertToMeans(h_sum, h_count)
    v_means = convertToMeans(v_sum, v_count)
    
    # Disable all regions
    left = h_center = right = bottom = v_center = top = False
    # Set the horizontal and vertical region, based on the highest mean
    h_max, v_max = max(h_means), max(v_means)
    left, h_center, right = [m == h_max for m in h_means]
    top, v_center, bottom = [m == v_max for m in v_means]

    # Create the output signal arrow and command
    end_point_x, end_point_y = center_point
    command = SleeveHandler.BASE_COMMAND + SleeveHandler.TAP

    if (left and right) or h_center:
        command += SleeveHandler.H_CENTER
    elif left:
        command += SleeveHandler.LEFT
        end_point_x -= ARROW_LENGTH
    elif right:
        command += SleeveHandler.RIGHT
        end_point_x += ARROW_LENGTH

    if (top and bottom) or v_center:
        command += SleeveHandler.V_CENTER
    elif top:
        command += SleeveHandler.TOP
        end_point_y -= ARROW_LENGTH
    elif bottom:
        command += SleeveHandler.BOTTOM
        end_point_y += ARROW_LENGTH

    if intensity == SleeveHandler.SOFT:
        command += SleeveHandler.DECREASE + "10"
    elif intensity == SleeveHandler.MEDIUM:
        command += SleeveHandler.DECREASE + "5"
    elif intensity == SleeveHandler.INTENSE:
        command += SleeveHandler.DECREASE + "0"
    elif intensity == SleeveHandler.OFF:
        command = ""

    return command, intensity, (end_point_x, end_point_y)

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
w, h = int(resolution[0]/GRID_COLUMNS), int(resolution[1]/GRID_ROWS)
grid = [((c*w, r*h), ((c+1)*w, (r+1)*h)) for r in range(GRID_ROWS) for c in range(GRID_COLUMNS)]

# Process the settings to create subplots
fig, axes = plt.subplots(nrows=GRID_ROWS, ncols=GRID_COLUMNS, sharey=True, sharex=True)
axes = list(chain.from_iterable(axes))

# Create the window to display depth frame
if VISUALIZE_MODEL:
    if FULL_SCREEN_MODE:
        cv2.namedWindow("depth", cv2.WINDOW_NORMAL)
        cv2.setWindowProperty("depth", cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
    else:
        cv2.namedWindow("depth")
center_point = (resolution[0] // 2, resolution[1] // 2)


############################## Initialize SleeveHandler ##############################

sleeveHandler = SleeveHandler()
sleeveHandler.setLeftHandMode(LEFT_HANDED)


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
        if CREATE_SNAPSHOT and frame_count >= 100:
            with open("stored_depthFrame.bin", "wb") as file:
                np.save(file, depthFrame, allow_pickle=True)
            time.sleep(1)
            CREATE_SNAPSHOT = False


        # Process the data into blocks
        blocks = blockshaped(depthFrame, h, w)
        # Drop all zeroes from data
        blocks = np.array(list(map(lambda block: block[block > 0], blocks)), dtype=object)
        # Process the blocks into singular danger values (per block)
        values = list(map(measure, blocks))

        # Get output signal and arrow
        danger_levels = list(map(setGridSignals, values))
        command, intensity, endpoint = getOutputSignal(danger_levels, center_point)

        if command != "":
            sleeveHandler.processSignal(command, intensity)


        if VISUALIZE_MODEL:
            # Process the frame to be shown
            depthFrameColor = cv2.normalize(depthFrame, None, 255, 0, cv2.NORM_INF, cv2.CV_8UC1)
            depthFrameColor = cv2.equalizeHist(depthFrameColor)
            depthFrameColor = cv2.applyColorMap(depthFrameColor, cv2.COLORMAP_OCEAN)

            # Display the grid layout and information
            if SHOW_GRID:
                for i, (pos, size) in enumerate(grid):
                    color = colormap[danger_levels[i]]
                    cv2.putText(depthFrameColor, str(values[i]), (pos[0]+5, size[1]-5), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color)
                    cv2.rectangle(depthFrameColor, pos, size, color, cv2.FONT_HERSHEY_SIMPLEX)

            # Display the depthframe
            if SHOW_ARROW:
                if endpoint != center_point:
                    cv2.arrowedLine(depthFrameColor, center_point, endpoint, colormap[intensity], 3)
                elif command != "":
                    cv2.circle(depthFrameColor, center_point, 10, colormap[intensity], 3)
            cv2.imshow("depth", depthFrameColor)

            # Wait for 'q' keypress on the depth frame window to close
            key = cv2.waitKey(1)
            if key == ord('q'):                 # Interrupt application
                break
            elif key == ord('a'):               # Toggle arrow
                SHOW_ARROW = not SHOW_ARROW
            elif key == ord('g'):               # Toggle grid
                SHOW_GRID = not SHOW_GRID
            elif key == ord('s'):               # Save snapshot of camera data
                CREATE_SNAPSHOT = not CREATE_SNAPSHOT
            elif key == ord('p'):               # Save screenshot
                filename = "./screenshots/screenshot-" + str(time.strftime("%d_%m_%Y-%H_%M_%S")) + ".png"
                print("\nSaving Screenshot:\n" + filename)
                print("Success:", cv2.imwrite(filename, depthFrameColor))
                time.sleep(1)


        if PLOT_DATA:
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