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
POE_PD69200_MSG_SUB1_SUPPLY = 0x0B
POE_PD69200_MSG_SUB1_SAVE_CONFIG = 0x0F
POE_PD69200_MSG_SUB1_VERSIONZ = 0x1E
POE_PD69200_MSG_SUB1_SYSTEM_STATUS = 0xD0
POE_PD69200_MSG_SUB1_TEMP_MATRIX = 0x43
POE_PD69200_MSG_SUB1_CH_MATRIX = 0x44
POE_PD69200_MSG_SUB1_RESET = 0x55
POE_PD69200_MSG_SUB1_INDV_MSK = 0x56
POE_PD69200_MSG_SUB1_BT_PORTS_PARAMETERS = 0xC0
POE_PD69200_MSG_SUB1_BT_PORTS_CLASS = 0xC4
POE_PD69200_MSG_SUB1_BT_PORTS_MEASUREMENT = 0xC5

# PD69200 Message - Byte 5: SUB2
POE_PD69200_MSG_SUB2_MAIN = 0x17
POE_PD69200_MSG_SUB2_SW_VERSION = 0x21
POE_PD69200_MSG_SUB2_PWR_BUDGET = 0x57
POE_PD69200_MSG_SUB2_TOTAL_PWR = 0x60

#Port Mode CFG2
# BITS[3:0] BT Port PM Mode
POE_PD69200_MSG_DATA_PORT_MODE_DYNAMIC = 0x0
POE_PD69200_MSG_DATA_PORT_MODE_TPPL_BT = 0x01
POE_PD69200_MSG_DATA_PORT_MODE_DYNAMIC_NON_LLDP_CDP_AUTO_AND_TPPL_BT_LLDP_CDP_AUTO = 0x02
POE_PD69200_MSG_DATA_PORT_MODE_NOT_CHANGE = 0x0F
# BIT[7:4] Class Error Operation Select
POE_PD69200_MSG_DATA_PORT_CLASS_ERROR_DISABLE = 0x0
POE_PD69200_MSG_DATA_PORT_CLASS_SSPD_3_DSPD_3 = 0x10 
POE_PD69200_MSG_DATA_PORT_CLASS_SSPD_4_DSPD_3 = 0x20
POE_PD69200_MSG_DATA_PORT_CLASS_SSPD_6_DSPD_4 = 0x30
POE_PD69200_MSG_DATA_PORT_CLASS_SSPD_8_DSPD_5 = 0x40
POE_PD69200_MSG_DATA_PORT_CLASS_ERROR_NOT_CHANGE = 0xF0

#Port Operation Mode
POE_PD69200_MSG_DATA_PORT_OP_MODE_NOT_CHANGE = 0xFF

#Add poower for Port Mode, if Port Operation Mode is 0xFF, don't care

#Priority
POE_PD69200_MSG_DATA_PORT_PRIORITY_CRIT = 0x01
POE_PD69200_MSG_DATA_PORT_PRIORITY_HIGH = 0x02
POE_PD69200_MSG_DATA_PORT_PRIORITY_LOW = 0x03
POE_PD69200_MSG_DATA_PORT_PRIORITY_NO_CHANGE = 0xFF

POE_PD69200_MSG_DATA_CMD_ENDIS_ONLY = 0
POE_PD69200_MSG_DATA_CMD_DISABLE = 0
POE_PD69200_MSG_DATA_CMD_ENABLE = 1
POE_PD69200_MSG_DATA_CMD_No_CHAGNE = 0xF

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

TBL_CLASS_TO_CFG = {0x0: "0",
                    0x1: "1",
                    0x2: "2",
                    0x3: "3",
                    0x4: "4",
                    0x5: "5",
                    0x6: "6",
                    0x7: "7",
                    0x8: "8",
                    0xc: "Non"}
   

# Port Operation Mode as Protocol
# 802.3BT, 802.3AF/AT or Non-Compliant
TBL_PROTOCOL_TO_CFG = {0x00  : "IEEE802.3BT",
                       0x01  : "IEEE802.3BT",
                       0x02  : "IEEE802.3BT",
                       0x03  : "IEEE802.3BT",
                       0x09  : "IEEE802.3AF/AT",
                       0x10  : "Non-Compliant",
                       0x11  : "Non-Compliant",
                       0x12  : "Non-Compliant",
                       0x13  : "Non-Compliant",
                       0x14  : "Non-Compliant",
                       0x15  : "Non-Compliant",
                       0x20  : "Non-Compliant",
                       0x21  : "Non-Compliant",
                       0x22  : "Non-Compliant",
                       0x23  : "Non-Compliant",
                       0x24  : "Non-Compliant",
                       0x25  : "Non-Compliant",
                       0x26  : "Non-Compliant",
                       0x27  : "Non-Compliant",
                       0x30  : "Non-Compliant",
                       0x50  : "Non-Compliant",
                       0xFF  : "Non-Compliant"}

TBL_STATUS_TO_CFG = {0x06: "Port Off (0x06)",
                     0x07: "Port Off (0x07)",
                     0x08: "Port Off (0x08)",
                     0x0C: "Port Off (0x0C)",
                     0x11: "Port Undef (0x11)",
                     0x12: "Port Off (0x12)",
                     0x1A: "Port Off (0x1A)",
                     0x1B: "Port Off (0x1B)",
                     0x1C: "Port Off (0x1C)",
                     0x1E: "Port Off (0x1E)",
                     0x1F: "Port Off (0x1F)",
                     0x20: "Port Off (0x20)",
                     0x21: "Port Off (0x21)",
                     0x22: "Port Off (0x22)",
                     0x24: "Port Off (0x24)",
                     0x25: "Port Off (0x25)",
                     0x26: "Port Off (0x26)",
                     0x34: "Port Off (0x34)",
                     0x35: "Port Off (0x35)",
                     0x36: "Port Off (0x36)",
                     0x37: "Unknown (0x37)",
                     0x3C: "Power M-S (0x3C)",
                     0x3D: "Power M-S (0x3D)",
                     0x41: "Power Err (0x41)",
                     0x43: "Port Off (0x43)",
                     0x44: "Port Off (0x44)",
                     0x45: "Port Off (0x45)",
                     0x46: "Port Off (0x46)",
                     0x47: "PWR Error (0x47)",
                     0x48: "Port Off (0x48)",
                     0x49: "Port Off (0x49)",
                     0x4A: "Port Off (0x4A)",
                     0x4B: "Port Off (0x4B)",
                     0x4C: "Port Off (0x4C)",
                     0x80: "2P Port-D (0x80)",
                     0x81: "2P Port-D (0x81)",
                     0x82: "4P Port-D (0x82)",
                     0x83: "4P Port-D (0x83)",
                     0x84: "4P Port-D (0x84)",
                     0x85: "4P Port-D (0x85)",
                     0x86: "4P Port-D (0x86)",
                     0x87: "4P Port-D (0x87)",
                     0x88: "4P Port-D (0x88)",
                     0x89: "4P Port-D (0x89)",
                     0x90: "Force PWR (0x90)",
                     0x91: "Force PWR (0x91)",
                     0xA0: "Force PWR-E (0xA0)",
                     0xA7: "CONN Error (0xA7)",
                     0xA8: "Open (0xA8)"}
