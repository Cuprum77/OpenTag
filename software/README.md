---

# Software Overview

This project uses [Macless-Haystack](https://github.com/dchristl/macless-haystack). Follow their instructions to set up a server and use the keys you generate to view the location of your tags. The simplest setup is to run the server on a Raspberry Pi with Tailscale and use the [Macless-Haystack](https://github.com/dchristl/macless-haystack) Android app or web viewer for access. With Tailscale, you can use a hostname like `https://myrpi.tail12345.ts.net:6176/` as the server URL in those. To access the locations from your server, you simply need to connect the client device to the Tailnet.

---

# Firmware Installation

The firmware is written in C/C++ using STM32CubeIDE. You don't need to compile the firmware if you are fine with the default settings:
- 1 key broadcast per minute
- No additional features (yet)

To modify the key refresh rate, change the RTC wakeup [here](TODO).

---

### Wireless Stack Installation

1. **Download STM32CubeProgrammer**:  
   Go [here](https://www.st.com/en/development-tools/stm32cubeprog.html) to download the STM32CubeProgrammer.  

2. **Prepare Your Hardware**:  
   Connect your board to a debugger, and connect the debugger to your computer.

3. **Install the Wireless Stack**:  
   Follow the instructions outlined:
   - [STM32WB BLE Hardware Setup](https://wiki.st.com/stm32mcu/wiki/Connectivity:STM32WB_BLE_Hardware_Setup)
   - [YouTube Tutorial](https://www.youtube.com/watch?v=-xYoI84zJew&t=568s)  

   Use these guides to:
   - Update the FUS
   - Upload the **full** wireless stack
   - Start the wireless stack

**⚠️Important⚠️**

Make absolutely sure you start the wireless stack after you flash it, without pressing that button in STM32CubeProgrammer the BLE will not work at all!

---

### Firmware Installation

Once the wireless stack is set up, proceed as follows:

1. Open the **"Erasing & Programming"** section in STM32CubeProgrammer.  
2. Select the binary file located [here](TODO).  
3. Ensure all checkmarks are **unchecked**.  
4. Click **Start Programming**.  

Now the device has the firmware, but the keys still need to be installed.

---

### Key Installation

1. **Generate Keys**:  
   Use the [Macless-Haystack](https://github.com/dchristl/macless-haystack) project, specifically the [generate_keys.py](https://github.com/dchristl/macless-haystack/releases/latest/download/generate_keys.py) script, to produce a `.bin` file containing the keys.  
   *(Note: Generating multiple keys in the file is left as an exercise for the reader.)*

2. **Program the Keys**:  
   - Open the **"Erasing & Programming"** section in STM32CubeProgrammer.  
   - Select the generated `.bin` file.  
   - Set the following options:
     - **Check**: "Skip flash erase before programming" (should be blue).
     - **Start Address**: `0x0803F000`.  
   - Click **Start Programming**.

The keys are now programmed. Disconnect power and the programmer from the device, ⚠️**DISCHARGE THE CAPACITORS**⚠️, insert a battery, and the device should start broadcasting your keys.

---

## Verifying Tag Functionality

To verify the tag is working:
1. Use [STBLEToolbox](https://www.st.com/en/embedded-software/stbletoolbox.html) on your phone. Place your phone right next to the tag, and put it into scanning mode and wait for several minutes.  
2. The tag will report itself as an Apple Device with the following advertisement payload:  
   - **Length**: 26  
   - **Type**: 0xFF  
   - **Value**: 0x...  
3. The app should identify it as an iBeacon.

--- 
