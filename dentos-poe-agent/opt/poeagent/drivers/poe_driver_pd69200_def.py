'''
Copyright 2021 Delta Electronic Inc.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
'''

# PD69200 Global Definitions
POE_PD69200_MSG_LEN = 15
POE_PD69200_MSG_CSUM_LEN = 2
POE_PD69200_MSG_N = 0x4E
POE_PD69200_COMM_RETRY_TIMES = 6

# PD69200 Message Structure
POE_PD69200_MSG_OFFSET_KEY = 0
POE_PD69200_MSG_OFFSET_ECHO = 1
POE_PD69200_MSG_OFFSET_SUB = 2
POE_PD69200_MSG_OFFSET_SUB1 = 3
POE_PD69200_MSG_OFFSET_SUB2 = 4
POE_PD69200_MSG_OFFSET_DATA5 = 5
POE_PD69200_MSG_OFFSET_DATA6 = 6
POE_PD69200_MSG_OFFSET_DATA7 = 7
POE_PD69200_MSG_OFFSET_DATA8 = 8
POE_PD69200_MSG_OFFSET_DATA9 = 9
POE_PD69200_MSG_OFFSET_DATA10 = 10
POE_PD69200_MSG_OFFSET_DATA11 = 11
POE_PD69200_MSG_OFFSET_DATA12 = 12
POE_PD69200_MSG_OFFSET_CSUM_H = 13
POE_PD69200_MSG_OFFSET_CSUM_L = 14

# PD69200 Message - Byte 1: KEY
POE_PD69200_MSG_KEY_COMMAND = 0x00
POE_PD69200_MSG_KEY_PROGRAM = 0x01
POE_PD69200_MSG_KEY_REQUEST = 0x02
POE_PD69200_MSG_KEY_TELEMETRY = 0x03
POE_PD69200_MSG_KEY_TEST = 0x04
POE_PD69200_MSG_KEY_REPORT = 0x52

# PD69200 Message - Byte 3: SUB
POE_PD69200_MSG_SUB_CHANNEL = 0x05
POE_PD69200_MSG_SUB_E2 = 0x06
POE_PD69200_MSG_SUB_GLOBAL = 0x07
POE_PD69200_MSG_SUB_RESOTRE_FACT = 0x2D
POE_PD69200_MSG_SUB_USER_BYTE = 0x41
POE_PD69200_MSG_SUB_FLASH = 0xFF

# PD69200 Message - Byte 4: SUB1
POE_PD69200_MSG_SUB1_PRIORITY = 0x0A
POE_PD69200_MSG_SUB1_SUPPLY = 0x0B
POE_PD69200_MSG_SUB1_EN_DIS = 0x0C
POE_PD69200_MSG_SUB1_PORT_STATUS = 0x0E
POE_PD69200_MSG_SUB1_SAVE_CONFIG = 0x0F
POE_PD69200_MSG_SUB1_VERSIONZ = 0x1E
POE_PD69200_MSG_SUB1_PARAMZ = 0x25
POE_PD69200_MSG_SUB1_PORTS_STATUS1 = 0x31
POE_PD69200_MSG_SUB1_PORTS_STATUS2 = 0x32
POE_PD69200_MSG_SUB1_PORTS_STATUS3 = 0x33
POE_PD69200_MSG_SUB1_SYSTEM_STATUS = 0x3D
POE_PD69200_MSG_SUB1_TEMP_MATRIX = 0x43
POE_PD69200_MSG_SUB1_CH_MATRIX = 0x44
POE_PD69200_MSG_SUB1_PORTS_STATUS4 = 0x47
POE_PD69200_MSG_SUB1_PORTS_STATUS5 = 0x48
POE_PD69200_MSG_SUB1_PORTS_POW1 = 0x4B
POE_PD69200_MSG_SUB1_PORTS_POW2 = 0x4C
POE_PD69200_MSG_SUB1_PORTS_POW3 = 0x4D
POE_PD69200_MSG_SUB1_PORTS_POW4 = 0x4F
POE_PD69200_MSG_SUB1_PORTS_POW5 = 0x50
POE_PD69200_MSG_SUB1_RESET = 0x55
POE_PD69200_MSG_SUB1_INDV_MSK = 0x56
POE_PD69200_MSG_SUB1_DEV_PARAMS = 0x87
POE_PD69200_MSG_SUB1_PORTS_DLV_PWR = 0xC0

# PD69200 Message - Byte 5: SUB2
POE_PD69200_MSG_SUB2_MAIN = 0x17
POE_PD69200_MSG_SUB2_SW_VERSION = 0x21
POE_PD69200_MSG_SUB2_PWR_BUDGET = 0x57
POE_PD69200_MSG_SUB2_PWR_MANAGE_MODE = 0x5F
POE_PD69200_MSG_SUB2_TOTAL_PWR = 0x60

# PD69200 Message - Byte 6 to Byte 13: DATA
POE_PD69200_MSG_DATA_CLASS_0 = 0
POE_PD69200_MSG_DATA_CLASS_1 = 1
POE_PD69200_MSG_DATA_CLASS_2 = 2
POE_PD69200_MSG_DATA_CLASS_3 = 3
POE_PD69200_MSG_DATA_CLASS_4 = 4
POE_PD69200_MSG_DATA_CLASS_5 = 5

POE_PD69200_MSG_DATA_PROTOCOL_AF = 0
POE_PD69200_MSG_DATA_PROTOCOL_ATAF = 1
POE_PD69200_MSG_DATA_PROTOCOL_AOH = 2

POE_PD69200_MSG_DATA_PORT_TYPE_AF = 0
POE_PD69200_MSG_DATA_PORT_TYPE_AT = 1
POE_PD69200_MSG_DATA_PORT_TYPE_AOH = 2

POE_PD69200_MSG_DATA_CMD_ENDIS_ONLY = 0
POE_PD69200_MSG_DATA_CMD_DISABLE = 0
POE_PD69200_MSG_DATA_CMD_ENABLE = 1

POE_PD69200_MSG_DATA_PORT_PRIORITY_CRIT = 1
POE_PD69200_MSG_DATA_PORT_PRIORITY_HIGH = 2
POE_PD69200_MSG_DATA_PORT_PRIORITY_LOW = 3

POE_PD69200_MSG_DATA_PM1_DYNAMIC = 0
POE_PD69200_MSG_DATA_PM2_PPL = 0
POE_PD69200_MSG_DATA_PM3_NO_COND = 0

TBL_ENDIS_TO_CFG = {POE_PD69200_MSG_DATA_CMD_ENABLE : "enable",
                    POE_PD69200_MSG_DATA_CMD_DISABLE: "disable"}

TBL_ENDIS_TO_DRV = {"enable" : POE_PD69200_MSG_DATA_CMD_ENABLE,
                    "disable": POE_PD69200_MSG_DATA_CMD_DISABLE}

TBL_PRIORITY_TO_CFG = {POE_PD69200_MSG_DATA_PORT_PRIORITY_CRIT: "crit",
                       POE_PD69200_MSG_DATA_PORT_PRIORITY_HIGH: "high",
                       POE_PD69200_MSG_DATA_PORT_PRIORITY_LOW : "low"}

TBL_PRIORITY_TO_DRV = {"crit": POE_PD69200_MSG_DATA_PORT_PRIORITY_CRIT,
                       "high": POE_PD69200_MSG_DATA_PORT_PRIORITY_HIGH,
                       "low" : POE_PD69200_MSG_DATA_PORT_PRIORITY_LOW}

TBL_CLASS_TO_CFG = {POE_PD69200_MSG_DATA_CLASS_0: "0",
                    POE_PD69200_MSG_DATA_CLASS_1: "1",
                    POE_PD69200_MSG_DATA_CLASS_2: "2",
                    POE_PD69200_MSG_DATA_CLASS_3: "3",
                    POE_PD69200_MSG_DATA_CLASS_4: "4",
                    POE_PD69200_MSG_DATA_CLASS_5: "Err"}

TBL_PROTOCOL_TO_CFG = {POE_PD69200_MSG_DATA_PROTOCOL_AF  : "IEEE802.3AF",
                       POE_PD69200_MSG_DATA_PORT_TYPE_AT : "IEEE802.3AF/AT",
                       POE_PD69200_MSG_DATA_PORT_TYPE_AOH: "POH"}

TBL_STATUS_TO_CFG = {0x00: "Port On (0x00)",
                     0x01: "Port On (0x01)",
                     0x02: "Port On (0x02)",
                     0x03: "Port On (0x03)",
                     0x04: "Port On (0x04)",
                     0x06: "Port Off (0x06)",
                     0x07: "Port Off (0x07)",
                     0x08: "Port Off (0x08)",
                     0x0C: "Port Off (0x0C)",
                     0x11: "Port Undef (0x11)",
                     0x12: "Port Off (0x12)",
                     0x1A: "Port Off (0x1A)",
                     0x1B: "Port Off (0x1B)",
                     0x1C: "Port Off (0x1C)",
                     0x1D: "Port Off (0x1D)",
                     0x1E: "Port Off (0x1E)",
                     0x1F: "Port Off (0x1F)",
                     0x20: "Port Off (0x20)",
                     0x21: "Port Off (0x21)",
                     0x24: "Port Off (0x24)",
                     0x25: "Port Off (0x25)",
                     0x26: "Port Off (0x26)",
                     0x2B: "Force On (0x2B)",
                     0x2C: "Undef Err (0x2C)",
                     0x2D: "Volt Err (0x2D)",
                     0x2E: "Volt Err (0x2E)",
                     0x2F: "Dis PDU (0x2F)",
                     0x31: "Port Off (0x31)",
                     0x32: "Port Off (0x32)",
                     0x33: "Comm Err (0x33)",
                     0x34: "Port Off (0x34)",
                     0x35: "Port Off (0x35)",
                     0x36: "Port Off (0x36)",
                     0x37: "Unknown (0x37)",
                     0x38: "S/C (0x38)",
                     0x39: "Over Temp (0x39)",
                     0x3A: "Over Temp (0x3A)",
                     0x3C: "Overload (0x3C)",
                     0x3D: "Overload (0x3D)",
                     0x3E: "Overload (0x3E)",
                     0x3F: "Overload (0x3F)",
                     0x43: "Port Off (0x43)",
                     0x44: "Port Off (0x44)",
                     0x45: "Port Off (0x45)",
                     0x46: "Port Off (0x46)",
                     0x47: "Power Err (0x47)"}
