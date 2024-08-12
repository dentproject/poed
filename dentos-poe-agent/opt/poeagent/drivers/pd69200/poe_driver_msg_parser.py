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

from enum import Enum
from typing import Any

from poe_common import *
from poe_driver_def import *


class PoeMsgParser(object):
    """Map the driver engineering values to an
    application-facing format.
    """

    class MessageType(Enum):
        MSG_PORT_POWER_LIMIT = 1
        MSG_PORT_PRIORITY = 2
        MSG_PORT_STATUS = 3
        MSG_POWER_SUPPLY_PARAMS = 4
        MSG_PORT_MEASUREMENTS = 5
        MSG_SYSTEM_STATUS = 6
        MSG_ALL_PORTS_ENDIS = 7
        MSG_POE_DEVICE_STATUS = 8
        MSG_INDV_MASK = 9
        MSG_PM_METHOD = 10
        MSG_SW_VERSION = 11
        MSG_BT_PORT_MEASUREMENTS = 12
        MSG_BT_PORT_PARAMETERS = 13
        MSG_BT_SYSTEM_STATUS = 14
        MSG_BT_PORT_CLASS = 15
        MSG_ACTIVE_MATRIX = 16
        MSG_BT_ALL_PORTS_POWER = 17
        MSG_BT_PORT_STATUS = 18
        MSG_BT_LLDP_PSE_DATA = 19
        MSG_BT_LLDP_PD_DATA = 20
        MSG_TOTAL_POWER = 21
        MSG_LLDP_PSE_DATA = 22
        MSG_SYSTEM_STATUS2 = 23
        MSG_POWER_BANK = 24
        MSG_CMD_STATUS = 255

    def __to_word(self, byteH: int, byteL: int) -> int:
        return (byteH << 8 | byteL) & 0xFFFF

    def __parse_port_power_limit(self, msg: list) -> dict[str, int]:
        parsed_data = {
            PPL: self.__to_word(msg[POE_PD69200_MSG_OFFSET_SUB], msg[POE_PD69200_MSG_OFFSET_SUB1]),
            TPPL: self.__to_word(msg[POE_PD69200_MSG_OFFSET_SUB2], msg[POE_PD69200_MSG_OFFSET_DATA5]),
        }

        return parsed_data

    def __parse_port_priority(self, msg: list) -> dict[str, int]:
        parsed_data = {PRIORITY: msg[POE_PD69200_MSG_OFFSET_SUB]}

        return parsed_data

    def __parse_port_status(self, msg: list) -> dict[str, int]:
        parsed_data = {
            ENDIS: msg[POE_PD69200_MSG_OFFSET_SUB],
            STATUS: msg[POE_PD69200_MSG_OFFSET_SUB1],
            LATCH: msg[POE_PD69200_MSG_OFFSET_DATA5],
            CLASS: msg[POE_PD69200_MSG_OFFSET_DATA6],
            PROTOCOL: msg[POE_PD69200_MSG_OFFSET_DATA10],
            EN_4PAIR: msg[POE_PD69200_MSG_OFFSET_DATA11],
        }

        return parsed_data

    def __parse_bt_port_status_parameters(self, msg: list) -> dict[str, int]:
        parsed_data = {
            STATUS: msg[POE_PD69200_MSG_OFFSET_SUB],
            ENDIS: msg[POE_PD69200_MSG_OFFSET_SUB1],
            OPERATION_MODE: msg[POE_PD69200_MSG_OFFSET_DATA5],
            PRIORITY: msg[POE_PD69200_MSG_OFFSET_DATA7],
        }

        return parsed_data

    def __parse_bt_port_status(self, msg: list) -> dict[str, int]:
        parsed_data = {
            STATUS: msg[POE_PD69200_MSG_OFFSET_SUB],
            ENDIS: (msg[POE_PD69200_MSG_OFFSET_SUB1] & 0x0F),
            ASSIGNED_CLASS_ALT_A: (msg[POE_PD69200_MSG_OFFSET_SUB2] >> 4),
            ASSIGNED_CLASS_ALT_B: (msg[POE_PD69200_MSG_OFFSET_SUB2] & 0x0F),
            POWER_CONSUMP: self.__to_word(
                msg[POE_PD69200_MSG_OFFSET_DATA5], msg[POE_PD69200_MSG_OFFSET_DATA6]
            ),
            SHUTDOWN_STATUS: msg[POE_PD69200_MSG_OFFSET_DATA9],
            PORT_EVENT: msg[POE_PD69200_MSG_OFFSET_DATA11],
        }

        return parsed_data

    def __parse_all_ports_endis(self, msg: list) -> dict[str, list[int]]:
        parsed_data = {ENDIS: []}
        all_ports_endis = [
            msg[POE_PD69200_MSG_OFFSET_SUB],  # port_7_0
            msg[POE_PD69200_MSG_OFFSET_SUB1],  # port_15_8
            msg[POE_PD69200_MSG_OFFSET_SUB2],  # port_23_16
            msg[POE_PD69200_MSG_OFFSET_DATA6],  # port_31_24
            msg[POE_PD69200_MSG_OFFSET_DATA7],  # port_39_32
            msg[POE_PD69200_MSG_OFFSET_DATA8],
        ]  # port_47_40

        for endis_group in all_ports_endis:
            for idx in range(8):
                port_endis = (endis_group >> idx) & 1
                parsed_data[ENDIS].append(port_endis)

        return parsed_data

    def __parse_bt_all_ports_power(self, msg: list) -> dict[str, list[int]]:
        """Get All Ports Delivering Power State message

        Not the same as enable/disable state on an AF/AT system.
        """
        parsed_data = {ENDIS: []}
        all_ports_endis = [
            msg[POE_PD69200_MSG_OFFSET_SUB],  # port_7_0
            msg[POE_PD69200_MSG_OFFSET_SUB1],  # port_15_8
            msg[POE_PD69200_MSG_OFFSET_SUB2],  # port_23_16
            msg[POE_PD69200_MSG_OFFSET_DATA5],  # port_31_24
            msg[POE_PD69200_MSG_OFFSET_DATA6],  # port_39_32
            msg[POE_PD69200_MSG_OFFSET_DATA7],
        ]  # port_47_40

        for endis_group in all_ports_endis:
            for idx in range(8):
                port_endis = (endis_group >> idx) & 1
                parsed_data[ENDIS].append(port_endis)

        return parsed_data

    def __parse_power_supply_params(self, msg: list) -> dict[str, int]:
        parsed_data = {
            POWER_CONSUMP: self.__to_word(
                msg[POE_PD69200_MSG_OFFSET_SUB], msg[POE_PD69200_MSG_OFFSET_SUB1]
            ),
            MAX_SD_VOLT: self.__to_word(
                msg[POE_PD69200_MSG_OFFSET_SUB2], msg[POE_PD69200_MSG_OFFSET_DATA5]
            ),
            MIN_SD_VOLT: self.__to_word(
                msg[POE_PD69200_MSG_OFFSET_DATA6], msg[POE_PD69200_MSG_OFFSET_DATA7]
            ),
            POWER_BANK: msg[POE_PD69200_MSG_OFFSET_DATA9],
            TOTAL_POWER: self.__to_word(
                msg[POE_PD69200_MSG_OFFSET_DATA10], msg[POE_PD69200_MSG_OFFSET_DATA11]
            ),
        }

        return parsed_data

    def __parse_total_power_params(self, msg: list) -> dict[str, int]:
        parsed_data = {
            POWER_CONSUMP: self.__to_word(
                msg[POE_PD69200_MSG_OFFSET_SUB], msg[POE_PD69200_MSG_OFFSET_SUB1]
            ),
            CALCULATED_POWER: self.__to_word(
                msg[POE_PD69200_MSG_OFFSET_SUB2], msg[POE_PD69200_MSG_OFFSET_DATA5]
            ),
            POWER_AVAIL: self.__to_word(
                msg[POE_PD69200_MSG_OFFSET_DATA6], msg[POE_PD69200_MSG_OFFSET_DATA7]
            ),
            POWER_LIMIT: self.__to_word(
                msg[POE_PD69200_MSG_OFFSET_DATA8], msg[POE_PD69200_MSG_OFFSET_DATA9]
            ),
            POWER_BANK: msg[POE_PD69200_MSG_OFFSET_DATA10],
        }

        return parsed_data

    def __parse_port_measurements(self, msg: list) -> dict[str, int]:
        parsed_data = {
            CURRENT: self.__to_word(msg[POE_PD69200_MSG_OFFSET_SUB2], msg[POE_PD69200_MSG_OFFSET_DATA5]),
            POWER_CONSUMP: self.__to_word(
                msg[POE_PD69200_MSG_OFFSET_DATA6], msg[POE_PD69200_MSG_OFFSET_DATA7]
            ),
            VOLTAGE: self.__to_word(msg[POE_PD69200_MSG_OFFSET_DATA9], msg[POE_PD69200_MSG_OFFSET_DATA10]),
        }

        return parsed_data

    def __parse_bt_port_measurements(self, msg: list) -> dict[str, int]:
        parsed_data = {
            CURRENT: self.__to_word(msg[POE_PD69200_MSG_OFFSET_SUB2], msg[POE_PD69200_MSG_OFFSET_DATA5]),
            POWER_CONSUMP: self.__to_word(
                msg[POE_PD69200_MSG_OFFSET_DATA6], msg[POE_PD69200_MSG_OFFSET_DATA7]
            ),
            VOLTAGE: self.__to_word(msg[POE_PD69200_MSG_OFFSET_DATA9], msg[POE_PD69200_MSG_OFFSET_DATA10]),
        }

        return parsed_data

    def __parse_system_status(self, msg: list) -> dict[str, int]:
        parsed_data = {
            CPU_STATUS1: msg[POE_PD69200_MSG_OFFSET_SUB],
            CPU_STATUS2: msg[POE_PD69200_MSG_OFFSET_SUB1],
            FAC_DEFAULT: msg[POE_PD69200_MSG_OFFSET_SUB2],
            GIE: msg[POE_PD69200_MSG_OFFSET_DATA5],
            PRIV_LABEL: msg[POE_PD69200_MSG_OFFSET_DATA6],
            USER_BYTE: msg[POE_PD69200_MSG_OFFSET_DATA7],
            DEVICE_FAIL: msg[POE_PD69200_MSG_OFFSET_DATA8],
            TEMP_DISCO: msg[POE_PD69200_MSG_OFFSET_DATA9],
            TEMP_ALARM: msg[POE_PD69200_MSG_OFFSET_DATA10],
            INTR_REG: self.__to_word(
                msg[POE_PD69200_MSG_OFFSET_DATA11], msg[POE_PD69200_MSG_OFFSET_DATA12]
            ),
        }

        return parsed_data

    def __parse_bt_system_status(self, msg: list) -> dict[str, int]:
        parsed_data = {
            CPU_STATUS2: msg[POE_PD69200_MSG_OFFSET_SUB1],
            FAC_DEFAULT: msg[POE_PD69200_MSG_OFFSET_SUB2],
            PRIV_LABEL: msg[POE_PD69200_MSG_OFFSET_DATA6],
            NVM_USER_BYTE: msg[POE_PD69200_MSG_OFFSET_DATA7],
            FOUND_DEVICE: msg[POE_PD69200_MSG_OFFSET_DATA8],
            EVENT_EXIST: msg[POE_PD69200_MSG_OFFSET_DATA12],
        }

        return parsed_data

    def __parse_poe_device_params(self, msg: list) -> dict[str, int]:
        parsed_data = {
            CSNUM: msg[POE_PD69200_MSG_OFFSET_SUB],
            STATUS: msg[POE_PD69200_MSG_OFFSET_DATA5],
            TEMP: msg[POE_PD69200_MSG_OFFSET_DATA9],
            TEMP_ALARM: msg[POE_PD69200_MSG_OFFSET_DATA10],
        }
        return parsed_data

    def __parse_indv_mask(self, msg: list) -> dict[str, int]:
        parsed_data = {ENDIS: msg[POE_PD69200_MSG_OFFSET_SUB]}

        return parsed_data

    def __parse_pm_method(self, msg: list) -> dict[str, int]:
        parsed_data = {
            PM1: msg[POE_PD69200_MSG_OFFSET_SUB],
            PM2: msg[POE_PD69200_MSG_OFFSET_SUB1],
            PM3: msg[POE_PD69200_MSG_OFFSET_SUB2],
        }

        return parsed_data

    def __parse_sw_version(self, msg: list) -> dict[str, int]:
        parsed_data = {
            PROD_NUM: msg[POE_PD69200_MSG_OFFSET_SUB2],
            SW_VERSION: self.__to_word(
                msg[POE_PD69200_MSG_OFFSET_DATA5], msg[POE_PD69200_MSG_OFFSET_DATA6]
            ),
        }
        return parsed_data

    def __parse_bt_port_class(self, msg: list) -> dict[str, int]:
        parsed_data = {
            MEASURED_CLASS_ALT_A: (msg[POE_PD69200_MSG_OFFSET_SUB2] >> 4),
            MEASURED_CLASS_ALT_B: (msg[POE_PD69200_MSG_OFFSET_SUB2] & 0x0F),
            REQUESTED_CLASS_ALT_A: (msg[POE_PD69200_MSG_OFFSET_DATA5] >> 4),
            REQUESTED_CLASS_ALT_B: (msg[POE_PD69200_MSG_OFFSET_DATA5] & 0x0F),
            ASSIGNED_CLASS_ALT_A: (msg[POE_PD69200_MSG_OFFSET_DATA8] >> 4),
            ASSIGNED_CLASS_ALT_B: (msg[POE_PD69200_MSG_OFFSET_DATA8] & 0x0F),
            TPPL: self.__to_word(msg[POE_PD69200_MSG_OFFSET_DATA9], msg[POE_PD69200_MSG_OFFSET_DATA10]),
        }

        return parsed_data

    def __parse_bt_port_lldp_pse_data(self, msg: list) -> dict[str, int]:
        parsed_data = {
            PSE_ALLOCATED_POWER_SINGLE_ALT_A: self.__to_word(
                msg[POE_PD69200_MSG_OFFSET_SUB], msg[POE_PD69200_MSG_OFFSET_SUB1]
            ),
            PSE_ALLOCATED_POWER_ALT_B: self.__to_word(
                msg[POE_PD69200_MSG_OFFSET_SUB2], msg[POE_PD69200_MSG_OFFSET_DATA5]
            ),
            PSE_MAX_POWER: self.__to_word(
                msg[POE_PD69200_MSG_OFFSET_DATA6], msg[POE_PD69200_MSG_OFFSET_DATA7]
            ),
            ASSIGNED_CLASS_ALT_A: (msg[POE_PD69200_MSG_OFFSET_DATA8] >> 4),
            ASSIGNED_CLASS_ALT_B: (msg[POE_PD69200_MSG_OFFSET_DATA8] & 0x0F),
            LAYER2_EXECUTION: (msg[POE_PD69200_MSG_OFFSET_DATA9] >> 4),
            LAYER2_USAGE: (msg[POE_PD69200_MSG_OFFSET_DATA9] & 0x0F),
            PSE_POWERING_STATUS: ((msg[POE_PD69200_MSG_OFFSET_DATA10] >> 2) & 0x03),
            PSE_POWER_PAIRS_EXT: (msg[POE_PD69200_MSG_OFFSET_DATA10] & 0x03),
            CABLE_LENGTH: (msg[POE_PD69200_MSG_OFFSET_DATA11] & 0x0F),
            PRIORITY: (msg[POE_PD69200_MSG_OFFSET_DATA12] & 0x0F),
        }

        return parsed_data

    def __parse_bt_port_lldp_pd_data(self, msg: list) -> dict[str, int]:
        parsed_data = {
            PD_REQUESTED_POWER_SINGLE: self.__to_word(
                msg[POE_PD69200_MSG_OFFSET_SUB], msg[POE_PD69200_MSG_OFFSET_SUB1]
            ),
            PD_REQUESTED_POWER_MODE_A: self.__to_word(
                msg[POE_PD69200_MSG_OFFSET_SUB2], msg[POE_PD69200_MSG_OFFSET_DATA5]
            ),
            PD_REQUESTED_POWER_MODE_B: self.__to_word(
                msg[POE_PD69200_MSG_OFFSET_DATA6], msg[POE_PD69200_MSG_OFFSET_DATA7]
            ),
            REQUESTED_CABLE_LENGTH: msg[POE_PD69200_MSG_OFFSET_DATA8],
        }

        return parsed_data

    def __parse_port_lldp_pse_data(self, msg: list) -> dict[str, int]:
        power_consumption = self.__to_word(msg[POE_PD69200_MSG_OFFSET_DATA11], msg[POE_PD69200_MSG_OFFSET_DATA12])
        parsed_data = {
            PSE_ALLOCATED_POWER: self.__to_word(
                msg[POE_PD69200_MSG_OFFSET_SUB], msg[POE_PD69200_MSG_OFFSET_SUB1]
            ),
            PD_REQUESTED_POWER: self.__to_word(
                msg[POE_PD69200_MSG_OFFSET_SUB2],
                msg[POE_PD69200_MSG_OFFSET_DATA5],
            ),
            LAYER2_USAGE: (power_consumption >> 11) & 0x3,
            LAYER2_EXECUTION: (power_consumption >> 13) & 0x1,
            CABLE_LENGTH: msg[POE_PD69200_MSG_OFFSET_DATA10],
            PRIORITY: msg[POE_PD69200_MSG_OFFSET_DATA6] & 0x3,
        }

        return parsed_data

    def __parse_cmd_status(self, msg: list) -> int:
        parsed_data = int.from_bytes(
            bytes([msg[POE_PD69200_MSG_OFFSET_SUB], msg[POE_PD69200_MSG_OFFSET_SUB1]]), byteorder="big"
        )

        return parsed_data

    def __parse_system_status2(self, msg: list) -> dict[str, int]:
        parsed_data = {
            GIE: msg[POE_PD69200_MSG_OFFSET_SUB1],
        }

        return parsed_data

    def __parse_active_matrix(self, msg: list) -> dict[str, int]:
        parsed_data = {
            ACTIVE_MATRIX_PHYA: msg[POE_PD69200_MSG_OFFSET_SUB],
            ACTIVE_MATRIX_PHYB: msg[POE_PD69200_MSG_OFFSET_SUB1],
        }

        return parsed_data

    def __parse_power_bank(self, msg: list) -> dict[str, int]:
        parsed_data = {
            POWER_LIMIT: self.__to_word(msg[POE_PD69200_MSG_OFFSET_SUB], msg[POE_PD69200_MSG_OFFSET_SUB1]),
            MAX_SD_VOLT: self.__to_word(
                msg[POE_PD69200_MSG_OFFSET_SUB2], msg[POE_PD69200_MSG_OFFSET_DATA5]
            ),
            MIN_SD_VOLT: self.__to_word(
                msg[POE_PD69200_MSG_OFFSET_DATA6], msg[POE_PD69200_MSG_OFFSET_DATA7]
            ),
        }

        return parsed_data

    def parse(self, msg: list, msg_type: MessageType) -> Any:
        if msg_type == self.MessageType.MSG_PORT_POWER_LIMIT:
            return self.__parse_port_power_limit(msg)
        elif msg_type == self.MessageType.MSG_PORT_PRIORITY:
            return self.__parse_port_priority(msg)
        elif msg_type == self.MessageType.MSG_PORT_STATUS:
            return self.__parse_port_status(msg)
        elif msg_type == self.MessageType.MSG_POWER_SUPPLY_PARAMS:
            return self.__parse_power_supply_params(msg)
        elif msg_type == self.MessageType.MSG_POWER_BANK:
            return self.__parse_power_bank(msg)
        elif msg_type == self.MessageType.MSG_TOTAL_POWER:
            return self.__parse_total_power_params(msg)
        elif msg_type == self.MessageType.MSG_PORT_MEASUREMENTS:
            return self.__parse_port_measurements(msg)
        elif msg_type == self.MessageType.MSG_SYSTEM_STATUS:
            return self.__parse_system_status(msg)
        elif msg_type == self.MessageType.MSG_ALL_PORTS_ENDIS:
            return self.__parse_all_ports_endis(msg)
        elif msg_type == self.MessageType.MSG_BT_ALL_PORTS_POWER:
            return self.__parse_bt_all_ports_power(msg)
        elif msg_type == self.MessageType.MSG_POE_DEVICE_STATUS:
            return self.__parse_poe_device_params(msg)
        elif msg_type == self.MessageType.MSG_INDV_MASK:
            return self.__parse_indv_mask(msg)
        elif msg_type == self.MessageType.MSG_PM_METHOD:
            return self.__parse_pm_method(msg)
        elif msg_type == self.MessageType.MSG_SW_VERSION:
            return self.__parse_sw_version(msg)
        elif msg_type == self.MessageType.MSG_BT_PORT_PARAMETERS:
            return self.__parse_bt_port_status_parameters(msg)
        elif msg_type == self.MessageType.MSG_BT_PORT_CLASS:
            return self.__parse_bt_port_class(msg)
        elif msg_type == self.MessageType.MSG_BT_PORT_STATUS:
            return self.__parse_bt_port_status(msg)
        elif msg_type == self.MessageType.MSG_BT_SYSTEM_STATUS:
            return self.__parse_bt_system_status(msg)
        elif msg_type == self.MessageType.MSG_BT_PORT_MEASUREMENTS:
            return self.__parse_bt_port_measurements(msg)
        elif msg_type == self.MessageType.MSG_LLDP_PSE_DATA:
            return self.__parse_port_lldp_pse_data(msg)
        elif msg_type == self.MessageType.MSG_BT_LLDP_PSE_DATA:
            return self.__parse_bt_port_lldp_pse_data(msg)
        elif msg_type == self.MessageType.MSG_BT_LLDP_PD_DATA:
            return self.__parse_bt_port_lldp_pd_data(msg)
        elif msg_type == self.MessageType.MSG_ACTIVE_MATRIX:
            return self.__parse_active_matrix(msg)
        elif msg_type == self.MessageType.MSG_CMD_STATUS:
            return self.__parse_cmd_status(msg)
        elif msg_type == self.MessageType.MSG_SYSTEM_STATUS2:
            return self.__parse_system_status2(msg)
