---

# Software Overview

This project is based on and uses [Macless-Haystack](https://github.com/dchristl/macless-haystack). Follow their instructions to set up a server and use the keys you generate to view the location of your tags. The simplest setup is to run the server on a Raspberry Pi with Tailscale and use the [Macless-Haystack](https://github.com/dchristl/macless-haystack) Android app or web viewer for access. With Tailscale, you can use a hostname like `https://myrpi.tail12345.ts.net:6176/` as the server URL in those. To access the locations from your server, you simply need to connect the client device to the Tailnet.

---

# Firmware Installation

The firmware is written in C/C++ using STM32CubeIDE. You don't need to compile the firmware if you are fine with the default settings:
- 1 key broadcast every 10 seconds
- 360 key re-use cycles (key changes once every hour)

You can modify these [here](STM32WB_BT_TAG/STM32_WPAN/App/app_ble.c#L177-L185).

---

### Wireless Stack Installation

1. **Download STM32CubeProgrammer**:  
   Go [here](https://www.st.com/en/development-tools/stm32cubeprog.html) to download the STM32CubeProgrammer.  

2. **Prepare Your Hardware**:  
   Short the `BOOT0` jumper to the 3V3 rail (2nd jumper from the top on the right side if viewed with antenna up).
   Connect your board to a debugger (preferably an ST-LINK), and connect the debugger to your computer.
   BOOT0 (top right) and the pinout of the debug connector (bottom right) is shown below:
   
   ![Image of the board layout](/software/images/pinout.png)

4. **Install the Wireless Stack and Firmware Update Service**:  
   Basically you need to visit [STM32WB_Copro_Wireless_Binaries](https://github.com/STMicroelectronics/STM32CubeWB/tree/master/Projects/STM32WB_Copro_Wireless_Binaries) and download 3 files:
   - `stm32wb3x_FUS_fw.bin`
   - `stm32wb3x_BLE_Stack_full_fw.bin`
   - `Release_Notes.html`
   
   Open `Release_Notes.html` in your browser. Here you need to find a table called "FW Upgrade Services Binary", it should include an entry for `stm32wb3x_FUS_fw.bin` and the address we need to program to depending on flash size. Use [STM32CubeProgrammer](https://www.st.com/en/development-tools/stm32cubeprog.html) to find what flash size you have (shown in bottom right after you connect to the target).

   ![Screenshot of STM32CubeProgrammer showing target info](/software/images/target_info.png)

   Open the **"Firmware Upgrade Services"** tab in STM32CubeProgrammer, click **"Start FUS"** under the **"WB Commands"** section, and then **"Read FUS infos"**. This will show you the current FUS version. Now select `stm32wb3x_FUS_fw.bin` in the file selector, enter the start address if STM32CubeProgrammer doesn't offer to auto detect the address. Hit **"Firmware Upgrade"** and wait for it to finish, once it's done once again click "Start FUS" and **"Read FUS infos"** and double check if the FUS version matches the one you downloaded.

   ![Screenshot of STM32CubeProgrammer](/software/images/cubeprog.png)

   Now you need to program the BLE stack. Take the `stm32wb3x_BLE_Stack_full_fw.bin` file and find it in `Release_Notes.html` to find the address depending on the flash size. Now select `stm32wb3x_BLE_Stack_full_fw.bin` in the file selector, enter the start address if STM32CubeProgrammer doesn't offer to auto detect the address and hit **"Firmware Upgrade"**. After this is done you should be able to "Read FUS infos" and see a **"STACK Version"** that shows an actual version and is not full of zeroes.

   **⚠️Important⚠️**

   Make absolutely sure you start the wireless stack after you flash it, without pressing that button in STM32CubeProgrammer the BLE will not work at all! The button to do so can be found in the **"Firmware Upgrade Services"** menu under **"WB Commands"**. Simply click **"Start Wireless Stack"** and wait for confirmation.

   Follow the instructions if you have any issues:
   - [STM32WB BLE Hardware Setup](https://wiki.st.com/stm32mcu/wiki/Connectivity:STM32WB_BLE_Hardware_Setup)
   - [YouTube Tutorial](https://www.youtube.com/watch?v=-xYoI84zJew&t=568s)  

---

### Firmware Installation

Once the wireless stack is set up, proceed as follows:

1. Open the **"Erasing & Programming"** section in STM32CubeProgrammer.  
2. Select the binary file located [here](STM32WB_BT_TAG/Release/STM32WB_BT_TAG.hex).  
3. Ensure that the **"Skip flash erase before programming"** checkmark is **unchecked**.  
4. Click **Start Programming**.  

Now the MCU has firmware, but keys still need to be installed.

---

### Key Installation

1. **Generate Keys**:  
   Use the [Macless-Haystack](https://github.com/dchristl/macless-haystack) project, specifically the [generate_keys.py](https://github.com/dchristl/macless-haystack/releases/latest/download/generate_keys.py) script, to produce a binary file containing the keys. Once the script produces the files, you need to find the smallest one without an extension and rename it to have a `.bin` at the end. Otherwise STM32CubeProgrammer will not accept it.
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

If you see the above device with a high power (-30 to -45dBm) when placed next to your phone then the tag is fully operational and transmitting BLE beacons.

--- 

## Getting Location Data

Please follow the instructions from [Macless-Haystack](https://github.com/dchristl/macless-haystack) on how to setup your apple account and server for receiving location data. In particular `Server setup` and `Frontend setup` should be read and followed thoroughly.
