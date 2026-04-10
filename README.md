# OpenTag

OpenTag is a project for building custom Bluetooth location finding beacons based on existing Apple and Google location finding networks. This repository contains the hardware and firmware for and STM32WB5x/35xx based "Polyglot" tag that can use both Google and Apple finding networks simultaneously. The repository also contains 3D printable enclosures for the tag as well as a docker based web interface for aggregating the results from the different location fetching backends.

## Backends
We did not invent or even do anything novel in terms of reverse engineering the Apple and Google finding networks. Thus, we rely on third party implementations of both to do the heavy lifting needed to make this project feasible, in particular:
- [Macless-Haystack](https://github.com/dchristl/macless-haystack) Used for fetching location data from the Apple finding network.
- [GoogleFindMyTools](https://github.com/leonboe1/GoogleFindMyTools) Used as a base for the Google finding network backend. (Note: A fork with additional features such as key rotation, compound tags and more was created based on GoogleFindMyTools and is used in this project, the fork is available [here](https://github.com/diminDDL/OpenTagBridge)).

## Hardware
The hardware design (located in the [hardware](./hardware) directory) for the Polyglot tag is based on the STM32WB5x/35xx series of microcontrollers and is powered with a single CR2032 battery. When actively advertising payloads it consumes around 3mA, in deep sleep that number drops to 3uA. By default the tag wakes up every 10 seconds for a ~25ms long advertising window and with a single 230mAh CR2032 cell the tag is expected to last for about 2 years and 6 months. The parameters can be adjusted for different use cases and battery life tradeoffs. The following formula can be used to estimate battery lifetime:

$$
I_{\mathrm{avg}}=
I_{\mathrm{active}}\cdot\frac{t_{\mathrm{active}}}{T_{\mathrm{cycle}}}
+
I_{\mathrm{sleep}}\cdot\left(1-\frac{t_{\mathrm{active}}}{T_{\mathrm{cycle}}}\right)
$$

$$
\text{Battery Life (years)} =
\frac{C_{\mathrm{bat}}}{I_{\mathrm{avg}}\cdot 24 \cdot 365}
$$

or equivalently

$$
\text{Battery Life (years)} =
\frac{C_{\mathrm{bat}}}
{\left(
I_{\mathrm{active}}\cdot\frac{t_{\mathrm{active}}}{T_{\mathrm{cycle}}}
+
I_{\mathrm{sleep}}\cdot\left(1-\frac{t_{\mathrm{active}}}{T_{\mathrm{cycle}}}\right)
\right)\cdot 24 \cdot 365}
$$

Where by default the parameters are:

$$
I_{\mathrm{active}} = 3.0\ \text{mA}
$$

$$
I_{\mathrm{sleep}} = 0.003\ \text{mA}
$$

$$
t_{\mathrm{active}} = 24\ \text{ms}
$$

$$
T_{\mathrm{cycle}} = 10\ \text{s}
$$

$$
C_{\mathrm{bat}} = 230\ \text{mAh}
$$

There is also a large capacitor bank on the board, it is optional, however it contains enough energy for a few advertisement bursts which can prevent the tag from resetting in high vibration environments.

The tag uses an integrated single part matching network from ST and a PCB antenna offering quite consistent RF performance without the need for manual tuning. A 4 layer stackup is used for better RF performance, but it could be simplified to 2 layers and a thinner stackup if needed.

The board also includes 2 LEDs, 2 jumpers for debugging and programming and an SWD pad compatible with a Tag-Connect.

## Firmware
See the [software](./software) directory for the firmware source code, build and programming instructions.

## Mechanical
The [3d_models](./3d_models) directory contains the 3D models for the tag itself, and the [enclosures](./enclosures) directory contains 3D models for enclosures.

## Aggregation and Web Interface
*TODO*

# Disclaimer
> [!CAUTION]
> This project is provided for educational and research purposes only. The authors and contributors disclaim liability and do not condone illegal, unauthorized, or unethical use, including unauthorized tracking or invasions of privacy.
> 
> Use of this project is entirely at your own risk. Interacting with Google or Apple location networks in undocumented or unsupported ways may violate platform rules or terms of service and may result in account penalties, service restrictions, or other consequences. The authors and contributors disclaim liability for misuse of this repository and for any damages or consequences resulting from its use.
