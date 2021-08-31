# DENT Project POE Agent - poed

Poe agent is a software package on dentOS that helps users to manage the POE chip on their board, and let system can automatically restore user configurations from cold boot, warm boot or even system crash.

Current test version: 0.4.0-alpha

Current Supported Platform: Delta tn48m-poe

Current Supported POE chip: Microsemi PD69200


# Software Architeture
![image](https://github.com/chenglin-tsai/poed/blob/main/poe_software_architecture.png)


# Folder Architeture
* poed.service – The configuration file of poed system service
* poecli – Show system/ports information and set the PoE chip using CLI
* poed – Run the configuration update routine periodically
* poe_driver_pd69200 – Provide the APIs for controlling the Mircosemi pd69200
* tn48m-poe-r0/poe_platform.py – Includes the platform PoE settings and initialization procedure on this platform
* smbus2 – The third party library used for i2c communications in python. (submodule)

![image](https://github.com/chenglin-tsai/poed/blob/main/poe_folder_architecture.png)
