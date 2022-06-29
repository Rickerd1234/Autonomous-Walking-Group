import socket
import time
from os import listdir

# Define Constants
COMMANDS_DIR = "./Own Code/Sleeve/commands/"
UDP_IP = "127.0.0.1"
UDP_PORT = 50000


# Define Status Codes
STATUS_ERROR = -1
STATUS_BUSY = -2

class SleeveHandler:
    BASE_COMMAND = "!PlayPattern"

    # Intensity levels
    OFF     = 0
    SOFT    = 1
    MEDIUM  = 2
    INTENSE = 3

    # Intensity decrease
    DECREASE = ",intensityIncrease=-"

    # Available patterns:
    STROKE_SLOW = ",stroke_down_slow"
    STROKE_FAST = ",stroke_down_fast"
    TAP         = ",double_tap"

    # Reverse direction:
    INVERT_VERTICAL = ",invertVertical=true"

    # Patterns for each area
    # RIGHT           = ",circumferenceCoorOffset=256"
    # H_CENTER        = ",circumferenceCoorOffset=512"
    # LEFT            = ",circumferenceCoorOffset=768"
    # BOTTOM          = ",verticalCoorOffset=1023"
    # V_CENTER        = ",verticalCoorOffset=512"
    # TOP             = ",verticalCoorOffset=0"

    RIGHT           = ",verticalCoorOffset=0"
    H_CENTER        = ",verticalCoorOffset=512"
    LEFT            = ",verticalCoorOffset=1023"
    BOTTOM          = ",circumferenceCoorOffset=768"
    V_CENTER        = ",circumferenceCoorOffset=1023"
    TOP             = ",circumferenceCoorOffset=256"

    # Extra delay dependent on intensity
    delays = {OFF: 0, SOFT:1, MEDIUM:0.5, INTENSE:0.1}

    # Define global variables
    def __init__(self):
        self.cmd_dict = {}
        self.readCommandFiles()
        # Initialize connection
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        self.busyUntil = time.time()
        self.leftHanded = False

    def setLeftHandMode(self, enabled):
        self.leftHanded = enabled

    # Define helper functions
    def sendCommand(self, command, pattern=True):
        # Make sure the previous pattern is finished
        if (self.busyUntil > time.time()): return STATUS_BUSY, "STATUS: BUSY"

        # Prepare the command for left handed use
        # TODO: Implement working approach of this
        #       It seems that invertHorizontal has no effect
        if self.leftHanded and pattern:
            command += ",invertHorizontal=true"
        
        # Send pattern command
        try:
            self.socket.sendto(bytes(command, "utf-8"), (UDP_IP, UDP_PORT))
            response = self.socket.recvfrom(4096)[0].decode("utf-8")

            # Update the busyUntil tracker
            duration = int(response.split(",")[1]) / 1000
            self.busyUntil = time.time() + duration + 0.05

            # Print issue
            if duration < 0:
                print("Issue occured with command: {}".format(command))

            return duration, response
        
        # Error handling
        except Exception as e:
            print("Command failed: {}".format(e))
            return STATUS_ERROR, "STATUS: ERROR!"


    # Process command files
    def readCommandFiles(self):
        # Reset current dictionary
        self.cmd_dict = {}
        # Read folder
        cmd_filenames = listdir(COMMANDS_DIR)

        for filename in cmd_filenames:
            with open(COMMANDS_DIR + filename, "r") as file:
                # Process the files
                lines = file.read().split("\n")
                description, command = [line for line in lines if len(line) > 0]
                # Add command and description
                self.cmd_dict[filename] = {
                    "description":description,
                    "command":command
                }

                # Add the fast stroke_down as a new command
                if "stroke_down_slow" in command:
                    description = description + (" snel")
                    command = command.replace("stroke_down_slow", "stroke_down_fast")
                    self.cmd_dict[filename + "s"] = {
                        "description":description,
                        "command":command
                    }


    # Show all the command ids and descriptions
    def showCommands(self):
        for id, cmd_data in self.cmd_dict.items():
            print(f"{id:10}: {cmd_data['description']}")

    # Run a command based on the id
    def runCommand(self, id):
        return self.sendCommand(self.cmd_dict[id]["command"])

    # Test all commands
    def testCommands(self):
        for cmd_id, cmd_obj in self.cmd_dict.items():
            duration, _ = self.runCommand(cmd_id)
            print("{:10s} {}".format(cmd_id, cmd_obj["description"]))
            time.sleep(duration + 5)

    # Process signals that are sent continuously
    def processSignal(self, command, intensity):
        duration, _ = self.sendCommand(command, pattern=True)
        # If it is executed, add the delay based on intensity
        if duration > 0: self.busyUntil += self.delays[intensity]
