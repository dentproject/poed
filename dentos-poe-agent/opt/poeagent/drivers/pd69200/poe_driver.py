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


import errno
import fcntl
import json
import os
import sys
import time
from abc import ABC, abstractmethod
from collections import OrderedDict
from io import TextIOWrapper
from time import perf_counter_ns
from typing import Any, Callable

from agent_constants import AgentConstants
from bus_driver import BusDriver
from filelock import FileLock
from pd69200.poe_driver_def import *
from pd69200.poe_driver_msg_parser import PoeMsgParser
from poe_common import *
from poe_log import PoeLog


class RxTxDesync(RuntimeError):
    pass


class PoeCommExclusiveLock:
    """Synchronize the access to the PoE chipset,
    using both an internal and an external lock
    """

    def __call__(self, comm):
        def wrap_comm(*args, **kargs):
            poe_driver: PoeDriver_microsemi_pd69200 = args[0]
            if not isinstance(poe_driver, PoeDriver_microsemi_pd69200):
                raise AssertionError("Invalid PoE driver supplied")
            try:
                poe_driver.bus_lock()
                result = comm(*args, **kargs)
            except Exception as e:
                raise e
            finally:
                poe_driver.bus_unlock()
            return result

        return wrap_comm


class StateContext:
    def __init__(self, path: str = AgentConstants.POE_COMM_STATE_PATH):
        self._path = path
        self._fd: TextIOWrapper
        self._data = {}

    def __enter__(self):
        if os.path.exists(self._path):
            self._fd = open(self._path, "rt+")
        else:
            self._fd = open(self._path, "w")
        fcntl.flock(self._fd, fcntl.LOCK_EX)

        self._fd.seek(0, 2)
        sz = self._fd.tell()
        self._fd.seek(0)

        if sz:
            try:
                self._data = json.load(self._fd) or {}
            except json.JSONDecodeError:
                self._data = {}
        else:
            self._data = {}

        return self._data

    def __exit__(self, t, v, tb):
        try:
            # if an exception was raised, do not update the state file
            if v is None:
                self._fd.seek(0)
                self._fd.truncate()
                json.dump(self._data, self._fd)
        finally:
            fcntl.flock(self._fd, fcntl.LOCK_UN)

        return None


# TODO: Add type hints and extract mixin for 802.3bt.
class PoeDriver_microsemi_pd69200(ABC):
    def __init__(
        self,
        bus_driver: BusDriver,
        port_count: int,
        max_shutdown_voltage: int,
        min_shutdown_voltage: int,
        guard_band: int,
        power_bank_to_str: Callable[[int], str],
    ):
        # Passed from the child HAL.
        self._bus_driver = bus_driver
        self._port_count = port_count
        self._max_shutdown_voltage = max_shutdown_voltage
        self._min_shutdown_voltage = min_shutdown_voltage
        self._guard_band = guard_band
        self._power_bank_to_str = power_bank_to_str

        self._log = PoeLog(debug_mode=os.isatty(sys.stdin.fileno()))

        self._bt_support = False
        # Minimum waiting time since last 15 bytes transmission and
        # before reading back the telemetry or report from the PoE
        # controller: 30ms
        self._msg_delay = 0.03
        # Message read timeout (nano-seconds)
        self._msg_read_timeout_ns = 1000000000
        # Minimum waiting time since last command report and before
        # sending a new command to the PoE controller
        self._msg_min_time_between_commands_sec = 0.03
        # Wait time after saving system setting: 50ms
        self._save_sys_delay = 0.05
        # Wait time after restoring to factory defaults: 100ms
        self._restore_factory_defaults_delay = 0.1
        # Wait time to clear up poe chip I2C buffer: 500ms
        self._clear_bus_buffer_delay = 0.5
        # Wake-up time delay after resetting the chip: 300ms
        self._reset_poe_chip_delay = 0.3

        if os.path.exists(AgentConstants.POE_CPLD_RESET_RQ_PATH):
            self._log.warn("PoE chipset reset via CPLD requested")
            self._reset_cpld()
            os.unlink(AgentConstants.POE_CPLD_RESET_RQ_PATH)

    @property
    def bt_support(self) -> bool:
        return self._bt_support

    def bus_lock(self) -> None:
        self._bus_driver.bus_lock()

    def bus_unlock(self) -> None:
        self._bus_driver.bus_unlock()

    def __calc_msg_echo(self):
        with StateContext() as data:
            echo = data.get("echo", 0x00)
            echo += 1
            if echo == 0xFF:
                echo = 0x00
            data["echo"] = echo

        return echo

    def __calc_msg_csum(self, msg):
        if len(msg) > POE_PD69200_MSG_LEN - POE_PD69200_MSG_CSUM_LEN:
            raise RuntimeError("Invalid POE message Length: %d" % len(msg))

        csum16 = 0
        for data in msg:
            csum16 += data
        csum16 = csum16 & 0xFFFF
        return [csum16 >> 8, csum16 & 0xFF]

    def __build_tx_msg(self, command):
        if len(command) > POE_PD69200_MSG_LEN - POE_PD69200_MSG_CSUM_LEN:
            raise RuntimeError("Invalid POE Tx command Length: %d" % len(command))

        tx_msg = command[:]
        lenN = POE_PD69200_MSG_LEN - len(tx_msg) - POE_PD69200_MSG_CSUM_LEN
        for _ in range(lenN):
            tx_msg.append(POE_PD69200_MSG_N)
        tx_msg += self.__calc_msg_csum(tx_msg)
        return tx_msg

    def __xmit(self, msg, delay):
        if len(msg) != POE_PD69200_MSG_LEN:
            raise RuntimeError("Invalid POE Tx message Length: %d" % len(msg))
        self._bus_driver.write_message(msg, delay)

    def __recv(self):
        return self._bus_driver.read_message()

    def __read_message(self, echo_byte):
        """Reading a message from PoE chipset in a safe way
        by handling possible errors.
        """
        ret_msg = []
        read_len = POE_PD69200_MSG_LEN
        start_time_ns = perf_counter_ns()
        byte_searched = 0
        while len(ret_msg) != POE_PD69200_MSG_LEN and perf_counter_ns() - start_time_ns < self._msg_read_timeout_ns:
            rx_msg = self._bus_driver.read(read_len)
            if len(ret_msg) == 0:
                for i, byte in enumerate(rx_msg):
                    if byte_searched == 0 and (
                        byte == POE_PD69200_MSG_KEY_TELEMETRY or byte == POE_PD69200_MSG_KEY_REPORT
                    ):
                        byte_searched = 1
                    elif byte_searched == 1:
                        if byte == echo_byte:
                            ret_msg.extend(rx_msg[i - 1 :])
                            read_len -= len(ret_msg)
                            if len(ret_msg) != POE_PD69200_MSG_LEN:
                                PoeLog().dbg(
                                    "Read (raw buff) : {0} / len={1}".format(conv_byte_to_hex(rx_msg), len(rx_msg))
                                )
                                PoeLog().dbg(
                                    "Read (out buff) : {0} / len={1}".format(conv_byte_to_hex(ret_msg), len(ret_msg))
                                )
                            break
                        else:
                            PoeLog().err("Faild to match second message byte: {0}".format(conv_byte_to_hex(rx_msg)))
                            byte_searched = 0
            elif len(ret_msg) < POE_PD69200_MSG_LEN:
                ret_msg.extend(rx_msg[0 : POE_PD69200_MSG_LEN - len(ret_msg)])
                PoeLog().dbg("Read (next read) : {0} / len={1}".format(conv_byte_to_hex(ret_msg), len(ret_msg)))
        if len(ret_msg) == POE_PD69200_MSG_LEN:
            csum = self.__calc_msg_csum(rx_msg[0:POE_PD69200_MSG_OFFSET_CSUM_H])
            if ret_msg[POE_PD69200_MSG_OFFSET_CSUM_H] != csum[0] or ret_msg[POE_PD69200_MSG_OFFSET_CSUM_L] != csum[1]:
                PoeLog().err("Read (out): {0}".format(conv_byte_to_hex(ret_msg)))
                PoeLog().err(f"Read CRC failed {ret_msg[POE_PD69200_MSG_OFFSET_CSUM_H]} != {csum[0]}")
                PoeLog().err(f"Read CRC failed {ret_msg[POE_PD69200_MSG_OFFSET_CSUM_L]} != {csum[1]}")
        else:
            PoeLog().err("Read (out) : {0} / len={1}".format(conv_byte_to_hex(ret_msg), len(ret_msg)))
        return ret_msg

    def __check_rx_msg(self, rx_msg, tx_msg):
        if rx_msg == None:
            raise RuntimeError("Received POE message is None")
        if len(rx_msg) != POE_PD69200_MSG_LEN:
            PoeLog().err("__check_rx_msg Send: {0}".format(conv_byte_to_hex(tx_msg)))
            PoeLog().err("__check_rx_msg Read: {0}".format(conv_byte_to_hex(rx_msg)))
            raise RuntimeError("Received POE message Length is invalid: %d" % len(rx_msg))
        if rx_msg.count(0x00) == POE_PD69200_MSG_LEN:
            raise RuntimeError("POE RX is not ready")

        tx_key, rx_key = (tx_msg[POE_PD69200_MSG_OFFSET_KEY], rx_msg[POE_PD69200_MSG_OFFSET_KEY])
        if (
            tx_key == POE_PD69200_MSG_KEY_COMMAND or tx_key == POE_PD69200_MSG_KEY_PROGRAM
        ) and rx_key != POE_PD69200_MSG_KEY_REPORT:
            PoeLog().err("Send: {0}".format(conv_byte_to_hex(tx_msg)))
            PoeLog().err("Read: {0}".format(conv_byte_to_hex(rx_msg)))
            raise RxTxDesync(
                "Key field in Tx/Rx message is mismatch, "
                "Tx key is %02x, Rx key should be %02x, but "
                "received %02x" % (tx_key, POE_PD69200_MSG_KEY_REPORT, rx_key)
            )
        if tx_key == POE_PD69200_MSG_KEY_REQUEST and rx_key != POE_PD69200_MSG_KEY_TELEMETRY:
            PoeLog().err("Send: {0}".format(conv_byte_to_hex(tx_msg)))
            PoeLog().err("Read: {0}".format(conv_byte_to_hex(rx_msg)))
            raise RxTxDesync(
                "Key field in Tx/Rx message is mismatch, "
                "Tx key is %02x, Rx key should be %02x, but "
                "received %02x" % (tx_key, POE_PD69200_MSG_KEY_TELEMETRY, rx_key)
            )

        tx_echo, rx_echo = (tx_msg[POE_PD69200_MSG_OFFSET_ECHO], rx_msg[POE_PD69200_MSG_OFFSET_ECHO])
        if rx_echo != tx_echo:
            PoeLog().err("Send: {0}".format(conv_byte_to_hex(tx_msg)))
            PoeLog().err("Read: {0}".format(conv_byte_to_hex(rx_msg)))
            raise RuntimeError(
                "Echo field in Tx/Rx message is mismatch, " "Tx Echo is %02x, Rx Echo is %02x" % (tx_echo, rx_echo)
            )

        csum = self.__calc_msg_csum(rx_msg[0:POE_PD69200_MSG_OFFSET_CSUM_H])
        if rx_msg[POE_PD69200_MSG_OFFSET_CSUM_H] != csum[0] or rx_msg[POE_PD69200_MSG_OFFSET_CSUM_L] != csum[1]:
            PoeLog().err("Send: {0}".format(conv_byte_to_hex(tx_msg)))
            PoeLog().err("Read: {0}".format(conv_byte_to_hex(rx_msg)))
            raise RuntimeError("Invalid checksum in POE Rx message")

    @PoeCommExclusiveLock()
    def __communicate(self, tx_msg, delay):
        retry = 0
        rx_msg = []
        while True:
            try:
                self.__xmit(tx_msg, delay)
                if retry > 0:
                    self._log.dbg("Send (retry: {0}): {1}".format(retry, conv_byte_to_hex(tx_msg)))
                rx_msg = self.__read_message(tx_msg[POE_PD69200_MSG_OFFSET_ECHO])
                self.__check_rx_msg(rx_msg, tx_msg)
                return rx_msg
            except OSError as e:
                # Handling case OSError: [Errno 6] No such device or address
                # https://issues.amazon.com/issues/IHMNEET-205
                self._log.err(f"__communicate Exception (retry {retry}) (OSError): {str(e)}")
                if retry != 0 and e.errno == errno.ENXIO:
                    self._log.err(f"__communicate exit current process to reopen all resources !!!!!!!!!!!!")
                    sys.exit(e.errno)
            except RxTxDesync as rxe:
                self._log.err(f"__communicate Exception (RxTxDesync): {str(rxe)}")
                self.__run_syncronization_protocol()
            except Exception as e:
                self._log.exc(f"__communicate Exception: {str(e)}")

            # Wait 0.5s to clear up I2C buffer
            time.sleep(self._clear_bus_buffer_delay)
            retry += 1
            if retry < POE_PD69200_COMM_RETRY_TIMES:
                # Increment echo byte
                command = tx_msg[0:POE_PD69200_MSG_OFFSET_DATA12]
                command[POE_PD69200_MSG_OFFSET_ECHO] = self.__calc_msg_echo()
                tx_msg = self.__build_tx_msg(command)
            else:
                raise RuntimeError("Failed to run the PoE serial communication protocol")

    def __run_communication_protocol(self, command, delay, msg_type=None) -> Any:
        tx_msg = self.__build_tx_msg(command)
        # An external lock is required as there are multiple Python
        # modules using the PoE driver.
        with FileLock(AgentConstants.POE_BUSY_FLAG_PATH):
            with StateContext() as data:
                last_send_key = data.get("last_send_key", None)

            # A pre-defined delay is required between two consecutive
            # commands.
            if (
                last_send_key == tx_msg[POE_PD69200_MSG_OFFSET_KEY]
                and tx_msg[POE_PD69200_MSG_OFFSET_KEY] == POE_PD69200_MSG_KEY_COMMAND
            ):
                time.sleep(self._msg_min_time_between_commands_sec)

            rx_msg = self.__communicate(tx_msg, delay)

            with StateContext() as data:
                data["last_send_key"] = tx_msg[POE_PD69200_MSG_OFFSET_KEY]
            # XXX teeny tiny race condition if multiple threads enter here
            # the last_send_key may not be updated correctly
            # Here we assume that the state update is quicker than the
            # _communicate() so we can be fairly confident that we will win the
            # race

        if rx_msg is not None and msg_type is not None:
            result = PoeMsgParser().parse(rx_msg, msg_type)
            return result

    def __run_syncronization_protocol(self) -> None:
        """This function will syncronise PoE Rx/Tx buffer by sending a query
        message (Get Interrupt Mask) and
        1) reading data until beginning of the message received (the pair
            [MESSAGE TYPE, ECHO])
        2) read the rest of the 15 bytes message.
        """
        self._log.dbg("__run_syncronization_protocol-->")
        time.sleep(self._msg_delay)
        command = [
            POE_PD69200_MSG_KEY_REQUEST,
            self.__calc_msg_echo(),
            POE_PD69200_MSG_SUB_GLOBAL,
            POE_PD69200_MSG_SUB1_GETINTERRUPTMASK,
        ]
        tx_msg = self.__build_tx_msg(command)
        self._log.dbg("__run_syncronization_protocol send: {0}".format(conv_byte_to_hex(command)))
        self.__xmit(tx_msg, self._msg_delay)

        max_read_left = POE_PD69200_MSG_LEN * 2
        message_byte = 0
        while max_read_left > 0:
            rx_byte = self._bus_driver.read(1)
            if rx_byte is None or len(rx_byte) != 1:
                raise RuntimeError("Invalid response message from read(1)")
            self._log.info("__run_syncronization_protocol: recv byte" + str(rx_byte))
            max_read_left -= 1
            if message_byte == 0:
                if rx_byte[0] == POE_PD69200_MSG_KEY_REPORT:
                    self._log.info("__run_syncronization_protocol: found first byte")
                    message_byte = 1
                    continue
            if message_byte == 1:
                if rx_byte[0] == command[1]:
                    self._log.info("__run_syncronization_protocol: found second byte")
                    rx_byte = self._bus_driver.read(13)
                    break
                else:
                    message_byte = 0
        self._log.dbg("__run_syncronization_protocol<--")

    def reset_poe(self):
        command = [
            POE_PD69200_MSG_KEY_COMMAND,
            self.__calc_msg_echo(),
            POE_PD69200_MSG_SUB_GLOBAL,
            POE_PD69200_MSG_SUB1_RESET,
            0x00,
            POE_PD69200_MSG_SUB1_RESET,
            0x00,
            POE_PD69200_MSG_SUB1_RESET,
        ]
        return self.__run_communication_protocol(
            command, self._reset_poe_chip_delay, PoeMsgParser.MessageType.MSG_CMD_STATUS
        )

    @abstractmethod
    def _reset_cpld(self) -> None:
        """Override this with the specific CPLD reset sequence for this chip,
        if any."""
        pass

    def restore_factory_defaults(self):
        command = [POE_PD69200_MSG_KEY_PROGRAM, self.__calc_msg_echo(), POE_PD69200_MSG_SUB_RESTORE_FACT]
        return self.__run_communication_protocol(
            command, self._restore_factory_defaults_delay, PoeMsgParser.MessageType.MSG_CMD_STATUS
        )

    def save_system_settings(self):
        command = [
            POE_PD69200_MSG_KEY_PROGRAM,
            self.__calc_msg_echo(),
            POE_PD69200_MSG_SUB_E2,
            POE_PD69200_MSG_SUB1_SAVE_CONFIG,
        ]
        return self.__run_communication_protocol(command, self._save_sys_delay, PoeMsgParser.MessageType.MSG_CMD_STATUS)

    def supports_bt_protocol(self, min_major_ver: int = 3) -> bool:
        """Determine if the current driver supports 802.3bt

        Args:
            min_major_ver (int, optional): Firmware major version.
            Defaults to 3

        Returns:
            bool: True if supported, False otherwise
        """
        poe_ver = self.get_poe_versions()
        major_ver = int(poe_ver.split(".")[1])
        if major_ver >= min_major_ver:
            self._bt_support = True
        else:
            self._bt_support = False

        return self._bt_support

    def __set_user_byte_to_save(self, user_val):
        command = [POE_PD69200_MSG_KEY_PROGRAM, self.__calc_msg_echo(), POE_PD69200_MSG_SUB_USER_BYTE, user_val]
        return self.__run_communication_protocol(command, self._save_sys_delay, PoeMsgParser.MessageType.MSG_CMD_STATUS)

    def set_system_status(self, priv_label):
        command = [
            POE_PD69200_MSG_KEY_COMMAND,
            self.__calc_msg_echo(),
            POE_PD69200_MSG_SUB_GLOBAL,
            POE_PD69200_MSG_SUB1_SYSTEM_STATUS,
            priv_label,
        ]
        return self.__run_communication_protocol(command, self._msg_delay, PoeMsgParser.MessageType.MSG_CMD_STATUS)

    def get_system_status(self):
        command = [
            POE_PD69200_MSG_KEY_REQUEST,
            self.__calc_msg_echo(),
            POE_PD69200_MSG_SUB_GLOBAL,
            POE_PD69200_MSG_SUB1_SYSTEM_STATUS,
        ]
        return self.__run_communication_protocol(command, self._msg_delay, PoeMsgParser.MessageType.MSG_SYSTEM_STATUS)

    def set_individual_mask(self, mask_num, enDis):
        command = [
            POE_PD69200_MSG_KEY_COMMAND,
            self.__calc_msg_echo(),
            POE_PD69200_MSG_SUB_GLOBAL,
            POE_PD69200_MSG_SUB1_INDV_MSK,
            mask_num,
            enDis,
        ]
        return self.__run_communication_protocol(command, self._msg_delay, PoeMsgParser.MessageType.MSG_CMD_STATUS)

    def get_individual_mask_regs(self, mask_num):
        command = [
            POE_PD69200_MSG_KEY_REQUEST,
            self.__calc_msg_echo(),
            POE_PD69200_MSG_SUB_GLOBAL,
            POE_PD69200_MSG_SUB1_INDV_MSK,
            mask_num,
        ]
        return self.__run_communication_protocol(command, self._msg_delay, PoeMsgParser.MessageType.MSG_INDV_MASK)

    def __get_software_version(self):
        command = [
            POE_PD69200_MSG_KEY_REQUEST,
            self.__calc_msg_echo(),
            POE_PD69200_MSG_SUB_GLOBAL,
            POE_PD69200_MSG_SUB1_VERSIONS,
            POE_PD69200_MSG_SUB2_SW_VERSION,
        ]
        return self.__run_communication_protocol(command, self._msg_delay, PoeMsgParser.MessageType.MSG_SW_VERSION)

    def set_temp_matrix(self, port_index, phy_port_a, phy_port_b=0xFF):
        command = [
            POE_PD69200_MSG_KEY_COMMAND,
            self.__calc_msg_echo(),
            POE_PD69200_MSG_SUB_CHANNEL,
            POE_PD69200_MSG_SUB1_TEMP_MATRIX,
            port_index,
            phy_port_a,
            phy_port_b,
        ]
        return self.__run_communication_protocol(command, self._msg_delay, PoeMsgParser.MessageType.MSG_CMD_STATUS)

    def get_temp_matrix(self, port_index):
        command = [
            POE_PD69200_MSG_KEY_REQUEST,
            self.__calc_msg_echo(),
            POE_PD69200_MSG_SUB_CHANNEL,
            POE_PD69200_MSG_SUB1_TEMP_MATRIX,
            port_index,
        ]
        return self.__run_communication_protocol(command, self._msg_delay)

    def program_active_matrix(self):
        command = [
            POE_PD69200_MSG_KEY_COMMAND,
            self.__calc_msg_echo(),
            POE_PD69200_MSG_SUB_GLOBAL,
            POE_PD69200_MSG_SUB1_TEMP_MATRIX,
        ]
        return self.__run_communication_protocol(command, self._msg_delay, PoeMsgParser.MessageType.MSG_CMD_STATUS)

    def get_active_matrix(self, port_index: int) -> dict[str, int]:
        command = [
            POE_PD69200_MSG_KEY_REQUEST,
            self.__calc_msg_echo(),
            POE_PD69200_MSG_SUB_CHANNEL,
            POE_PD69200_MSG_SUB1_CH_MATRIX,
            port_index,
        ]
        return self.__run_communication_protocol(command, self._msg_delay, PoeMsgParser.MessageType.MSG_ACTIVE_MATRIX)

    def set_port_type_and_sum_as_tppl(self, port_index: int, port_type: int, sum_as_tppl: int) -> int:
        """Set the port type and the Sum_as_TPPL field for a given port.

        Args:
            port_index (int): Port ID (0-based)
            type (int): Port type engineering value
            sum_as_tppl (int): Sum_as_TPPL engineering value

        Returns:
            int: 0 if successful. != 0 otherwise
        """
        command = [
            POE_PD69200_MSG_KEY_COMMAND,
            self.__calc_msg_echo(),
            POE_PD69200_MSG_SUB_CHANNEL,
            POE_PD69200_MSG_SUB1_PORT_4PAIR,
            port_index,
            POE_PD69200_MSG_N,
            0xFF,
            0xFF,
            0xFF,
            port_type,
            sum_as_tppl,
            POE_PD69200_MSG_N,
        ]
        return self.__run_communication_protocol(command, self._msg_delay, PoeMsgParser.MessageType.MSG_CMD_STATUS)

    def set_port_en_dis(self, port_index, en_dis):
        command = [
            POE_PD69200_MSG_KEY_COMMAND,
            self.__calc_msg_echo(),
            POE_PD69200_MSG_SUB_CHANNEL,
            POE_PD69200_MSG_SUB1_EN_DIS,
            port_index,
            POE_PD69200_MSG_DATA_CMD_ENDIS_ONLY | en_dis,
            POE_PD69200_MSG_DATA_PORT_TYPE_AT,
        ]
        return self.__run_communication_protocol(command, self._msg_delay, PoeMsgParser.MessageType.MSG_CMD_STATUS)

    def get_all_ports_en_dis(self) -> dict[str, list]:
        """Get all port enable/disable state, depending on the
        802.3bt support.

        Returns:
            dict: Parsed data
        """
        if self.bt_support:
            return self.__bt_get_all_ports_en_dis()

        command = [
            POE_PD69200_MSG_KEY_REQUEST,
            self.__calc_msg_echo(),
            POE_PD69200_MSG_SUB_GLOBAL,
            POE_PD69200_MSG_SUB1_EN_DIS,
        ]
        return self.__run_communication_protocol(command, self._msg_delay, PoeMsgParser.MessageType.MSG_ALL_PORTS_ENDIS)

    # port range: 0x00 to 0x2F, 'AllChannels' = 0x80
    def set_port_power_limit(self, port_index, power_limit):
        command = [
            POE_PD69200_MSG_KEY_COMMAND,
            self.__calc_msg_echo(),
            POE_PD69200_MSG_SUB_CHANNEL,
            POE_PD69200_MSG_SUB1_SUPPLY,
            port_index,
            power_limit >> 8,
            power_limit & 0xFF,
        ]
        return self.__run_communication_protocol(command, self._msg_delay, PoeMsgParser.MessageType.MSG_CMD_STATUS)

    def get_port_power_limit(self, port_index):
        command = [
            POE_PD69200_MSG_KEY_REQUEST,
            self.__calc_msg_echo(),
            POE_PD69200_MSG_SUB_CHANNEL,
            POE_PD69200_MSG_SUB1_SUPPLY,
            port_index,
        ]
        return self.__run_communication_protocol(
            command, self._msg_delay, PoeMsgParser.MessageType.MSG_PORT_POWER_LIMIT
        )

    def set_port_priority(self, port_index, priority):
        command = [
            POE_PD69200_MSG_KEY_COMMAND,
            self.__calc_msg_echo(),
            POE_PD69200_MSG_SUB_CHANNEL,
            POE_PD69200_MSG_SUB1_PRIORITY,
            port_index,
            priority,
        ]
        return self.__run_communication_protocol(command, self._msg_delay, PoeMsgParser.MessageType.MSG_CMD_STATUS)

    def get_port_priority(self, port_index):
        command = [
            POE_PD69200_MSG_KEY_REQUEST,
            self.__calc_msg_echo(),
            POE_PD69200_MSG_SUB_CHANNEL,
            POE_PD69200_MSG_SUB1_PRIORITY,
            port_index,
        ]
        return self.__run_communication_protocol(command, self._msg_delay, PoeMsgParser.MessageType.MSG_PORT_PRIORITY)

    def get_port_status(self, port_index):
        command = [
            POE_PD69200_MSG_KEY_REQUEST,
            self.__calc_msg_echo(),
            POE_PD69200_MSG_SUB_CHANNEL,
            POE_PD69200_MSG_SUB1_PORT_STATUS,
            port_index,
        ]
        return self.__run_communication_protocol(command, self._msg_delay, PoeMsgParser.MessageType.MSG_PORT_STATUS)

    def set_pm_method(self, pm1, pm2, pm3):
        command = [
            POE_PD69200_MSG_KEY_COMMAND,
            self.__calc_msg_echo(),
            POE_PD69200_MSG_SUB_GLOBAL,
            POE_PD69200_MSG_SUB1_SUPPLY,
            POE_PD69200_MSG_SUB2_PWR_MANAGE_MODE,
            pm1,
            pm2,
            pm3,
        ]
        return self.__run_communication_protocol(command, self._msg_delay, PoeMsgParser.MessageType.MSG_CMD_STATUS)

    def get_pm_method(self):
        command = [
            POE_PD69200_MSG_KEY_REQUEST,
            self.__calc_msg_echo(),
            POE_PD69200_MSG_SUB_GLOBAL,
            POE_PD69200_MSG_SUB1_SUPPLY,
            POE_PD69200_MSG_SUB2_PWR_MANAGE_MODE,
        ]
        return self.__run_communication_protocol(command, self._msg_delay, PoeMsgParser.MessageType.MSG_PM_METHOD)

    def get_total_power(self):
        command = [
            POE_PD69200_MSG_KEY_REQUEST,
            self.__calc_msg_echo(),
            POE_PD69200_MSG_SUB_GLOBAL,
            POE_PD69200_MSG_SUB1_SUPPLY,
            POE_PD69200_MSG_SUB2_TOTAL_PWR,
        ]
        return self.__run_communication_protocol(command, self._msg_delay, PoeMsgParser.MessageType.MSG_TOTAL_POWER)

    def set_power_bank(self, bank, power_limit):
        command = [
            POE_PD69200_MSG_KEY_COMMAND,
            self.__calc_msg_echo(),
            POE_PD69200_MSG_SUB_GLOBAL,
            POE_PD69200_MSG_SUB1_SUPPLY,
            POE_PD69200_MSG_SUB2_PWR_BUDGET,
            bank,
        ]
        command += [x for x in int(power_limit).to_bytes(2, byteorder="big")]
        command += [x for x in int(self._max_shutdown_voltage).to_bytes(2, byteorder="big")]
        command += [x for x in int(self._min_shutdown_voltage).to_bytes(2, byteorder="big")]
        command.append(self._guard_band)
        return self.__run_communication_protocol(command, self._msg_delay, PoeMsgParser.MessageType.MSG_CMD_STATUS)

    def get_power_bank(self, bank):
        command = [
            POE_PD69200_MSG_KEY_REQUEST,
            self.__calc_msg_echo(),
            POE_PD69200_MSG_SUB_GLOBAL,
            POE_PD69200_MSG_SUB1_SUPPLY,
            POE_PD69200_MSG_SUB2_PWR_BUDGET,
            bank,
        ]
        return self.__run_communication_protocol(command, self._msg_delay, PoeMsgParser.MessageType.MSG_POWER_BANK)

    def get_power_supply_params(self):
        command = [
            POE_PD69200_MSG_KEY_REQUEST,
            self.__calc_msg_echo(),
            POE_PD69200_MSG_SUB_GLOBAL,
            POE_PD69200_MSG_SUB1_SUPPLY,
            POE_PD69200_MSG_SUB2_MAIN,
        ]
        return self.__run_communication_protocol(
            command, self._msg_delay, PoeMsgParser.MessageType.MSG_POWER_SUPPLY_PARAMS
        )

    def get_port_measurements(self, port_index):
        command = [
            POE_PD69200_MSG_KEY_REQUEST,
            self.__calc_msg_echo(),
            POE_PD69200_MSG_SUB_CHANNEL,
            POE_PD69200_MSG_SUB1_PARAMS,
            port_index,
        ]
        return self.__run_communication_protocol(
            command, self._msg_delay, PoeMsgParser.MessageType.MSG_PORT_MEASUREMENTS
        )

    def get_poe_device_parameters(self, csnum):
        command = [
            POE_PD69200_MSG_KEY_REQUEST,
            self.__calc_msg_echo(),
            POE_PD69200_MSG_SUB_GLOBAL,
            POE_PD69200_MSG_SUB1_DEV_PARAMS,
            csnum,
        ]
        return self.__run_communication_protocol(
            command, self._msg_delay, PoeMsgParser.MessageType.MSG_POE_DEVICE_STATUS
        )

    def get_poe_versions(self):
        versions = self.__get_software_version()
        prod = str(versions.get(PROD_NUM))
        sw_ver = int(versions.get(SW_VERSION))
        major_ver = str(int(sw_ver // 100))
        minor_ver = str(int(sw_ver // 10) % 10)
        pa_ver = str(int(sw_ver % 10))
        return f"{prod}.{major_ver}.{minor_ver}.{pa_ver}"

    def get_current_power_bank(self):
        params = self.get_power_supply_params()
        return params.get(POWER_BANK)

    def get_poe_port(self, port_id):
        return PoePort(self, port_id)

    def get_ports_status(
        self, ports: list[int], more_info: bool = True, log_port_status: bool = False
    ) -> list[OrderedDict]:
        ports_info = []
        for port in ports:
            info = PoePort(self, port, log_port_status).get_current_status(more_info)
            ports_info.append(info)
        return ports_info

    def get_system_information(self, verbose: bool = True) -> OrderedDict:
        return PoeSystem(self, self._port_count, self._power_bank_to_str).get_current_status(verbose)

    def get_port_l2_pse_data(self, port_index) -> dict:
        """Get the Layer 2 PSE data necessary for advertising the
        power capabilities of the port via LLDP. This includes the PSE
        allocated power at PD input, the PD requested power, the cable length
        and the port priority

        Args:
            port_index (_type_): Port ID (0-based)

        Returns:
            dict: Parsed data
        """
        command = [
            POE_PD69200_MSG_KEY_REQUEST,
            self.__calc_msg_echo(),
            POE_PD69200_MSG_SUB_CHANNEL,
            POE_PD69200_BT_MSG_SUB1_LAYER2_LLDP_PSE,
            port_index,
        ]

        return self.__run_communication_protocol(command, self._msg_delay, PoeMsgParser.MessageType.MSG_LLDP_PSE_DATA)

    def bt_get_port_measurements(self, port_index):
        if not self._bt_support:
            raise AssertionError("The PoE chipset doesn't support 802.3bt")

        command = [
            POE_PD69200_MSG_KEY_REQUEST,
            self.__calc_msg_echo(),
            POE_PD69200_MSG_SUB_CHANNEL,
            POE_PD69200_BT_MSG_SUB1_PORTS_MEASUREMENT,
            port_index,
        ]
        return self.__run_communication_protocol(
            command, self._msg_delay, PoeMsgParser.MessageType.MSG_BT_PORT_MEASUREMENTS
        )

    def bt_get_port_parameters(self, port_index):
        if not self._bt_support:
            raise AssertionError("The PoE chipset doesn't support 802.3bt")

        command = [
            POE_PD69200_MSG_KEY_REQUEST,
            self.__calc_msg_echo(),
            POE_PD69200_MSG_SUB_CHANNEL,
            POE_PD69200_BT_MSG_SUB1_PORT_CONFIG,
            port_index,
        ]
        return self.__run_communication_protocol(
            command, self._msg_delay, PoeMsgParser.MessageType.MSG_BT_PORT_PARAMETERS
        )

    def bt_get_port_class(self, port_index: int) -> dict:
        """Get the BT port class, including the measured class,
        assigned class for both modes and the port TPPL

        Args:
            port_index (int): Port ID (0-based)

        Returns:
            dict: Parsed data
        """
        if not self._bt_support:
            raise AssertionError("The PoE chipset doesn't support 802.3bt")

        command = [
            POE_PD69200_MSG_KEY_REQUEST,
            self.__calc_msg_echo(),
            POE_PD69200_MSG_SUB_CHANNEL,
            POE_PD69200_BT_MSG_SUB1_PORTS_CLASS,
            port_index,
        ]
        return self.__run_communication_protocol(command, self._msg_delay, PoeMsgParser.MessageType.MSG_BT_PORT_CLASS)

    def bt_set_port_params(self, port_index: int, pm_mode: int, op_mode: int) -> int:
        """Set the power management mode and operation mode fields
        for a BT port

        Args:
            port_index (int): Port ID (0-based)
            pm_mode (int): Port PM mode engineering value
            op_mode (int): Port operation mode engineering value

        Returns:
            int: 0 if successful, != 0 otherwise
        """
        if not self._bt_support:
            raise AssertionError("The PoE chipset doesn't support 802.3bt")

        command = [
            POE_PD69200_MSG_KEY_COMMAND,
            self.__calc_msg_echo(),
            POE_PD69200_MSG_SUB_CHANNEL,
            POE_PD69200_BT_MSG_SUB1_PORT_CONFIG,
            port_index,
            POE_PD69200_BT_MSG_DATA_CMD_ENDIS_NO_CHANGE,
            pm_mode | POE_PD69200_BT_MSG_DATA_PORT_CLASS_ERROR_NO_CHANGE,
            op_mode,
            POE_PD69200_BT_MSG_DATA_PORT_NO_ADDITIONAL_POWER,
            POE_PD69200_BT_MSG_DATA_PORT_PRIORITY_NO_CHANGE,
        ]
        return self.__run_communication_protocol(command, self._msg_delay, PoeMsgParser.MessageType.MSG_CMD_STATUS)

    def bt_get_system_status(self):
        if not self._bt_support:
            raise AssertionError("The PoE chipset doesn't support 802.3bt")

        command = [
            POE_PD69200_MSG_KEY_REQUEST,
            self.__calc_msg_echo(),
            POE_PD69200_MSG_SUB_GLOBAL,
            POE_PD69200_BT_MSG_SUB1_SYSTEM_STATUS,
        ]
        return self.__run_communication_protocol(
            command, self._msg_delay, PoeMsgParser.MessageType.MSG_BT_SYSTEM_STATUS
        )

    def get_system_status2(self):
        command = [
            POE_PD69200_MSG_KEY_REQUEST,
            self.__calc_msg_echo(),
            POE_PD69200_MSG_SUB_GLOBAL,
            POE_PD69200_BT_MSG_SUB1_SYSTEM_STATUS2,
        ]
        return self.__run_communication_protocol(command, self._msg_delay, PoeMsgParser.MessageType.MSG_SYSTEM_STATUS2)

    def bt_set_port_en_dis(self, port_index, en_dis):
        if not self._bt_support:
            raise AssertionError("The PoE chipset doesn't support 802.3bt")

        command = [
            POE_PD69200_MSG_KEY_COMMAND,
            self.__calc_msg_echo(),
            POE_PD69200_MSG_SUB_CHANNEL,
            POE_PD69200_BT_MSG_SUB1_PORT_CONFIG,
            port_index,
            POE_PD69200_MSG_DATA_CMD_ENDIS_ONLY | en_dis,
            POE_PD69200_BT_MSG_DATA_PORT_MODE_NO_CHANGE | POE_PD69200_BT_MSG_DATA_PORT_CLASS_ERROR_NO_CHANGE,
            POE_PD69200_BT_MSG_DATA_PORT_OP_MODE_NO_CHANGE,
            POE_PD69200_BT_MSG_DATA_PORT_NO_ADDITIONAL_POWER,
            POE_PD69200_BT_MSG_DATA_PORT_PRIORITY_NO_CHANGE,
        ]
        return self.__run_communication_protocol(command, self._msg_delay, PoeMsgParser.MessageType.MSG_CMD_STATUS)

    def bt_get_port_status(self, port_index: int) -> dict:
        """Get the BT port status, including the enable/disable state,
        the assigned class for each mode, the measured port power, the
        last shutdown error status and the port event

        Args:
            port_index (int): Port ID (0-based)

        Returns:
            dict: Parsed data
        """
        if not self._bt_support:
            raise AssertionError("The PoE chipset doesn't support 802.3bt")

        command = [
            POE_PD69200_MSG_KEY_REQUEST,
            self.__calc_msg_echo(),
            POE_PD69200_MSG_SUB_CHANNEL,
            POE_PD69200_BT_MSG_SUB1_PORT_STATUS,
            port_index,
        ]
        return self.__run_communication_protocol(command, self._msg_delay, PoeMsgParser.MessageType.MSG_BT_PORT_STATUS)

    def __bt_get_all_ports_en_dis(self) -> dict:
        """Get all BT ports en/dis status
        This can be done, for the BT firmware, only by querying each port
        individually.

        Returns:
            dict: Parsed state
        """
        ports = list(range(self._port_count))
        statuses = self.get_ports_status(ports, False, False)
        return {ENDIS: [1 if status[ENDIS] == "enable" else 0 for status in statuses]}

    def bt_get_port_l2_lldp_pd_request(self, port_index: int) -> dict:
        """Get the BT Layer 2 PD power request, including the PD requested
        power for both modes and the cable length requirement

        Args:
            port_index (int): Port ID (0-based)

        Returns:
            dict: Parsed data
        """
        if not self._bt_support:
            raise AssertionError("The PoE chipset doesn't support 802.3bt")

        command = [
            POE_PD69200_MSG_KEY_REQUEST,
            self.__calc_msg_echo(),
            POE_PD69200_MSG_SUB_CHANNEL,
            POE_PD69200_BT_MSG_SUB1_BT_LAYER2_LLDP_PD,
            port_index,
        ]
        return self.__run_communication_protocol(command, self._msg_delay, PoeMsgParser.MessageType.MSG_BT_LLDP_PD_DATA)

    def bt_get_port_l2_lldp_pse_data(self, port_index: int) -> dict:
        """Get the BT Layer 2 PSE data necessary for advertising the
        power capabilities of the port via LLDP. This includes the PSE
        allocated power at PD input for both modes, the PSE max power,
        assigned class and the BT PSE powering status and
        the power pairs ext bits IEEE fields

        Args:
            port_index (int): Port ID (0-based)

        Returns:
            dict: Parsed data
        """
        if not self._bt_support:
            raise AssertionError("The PoE chipset doesn't support 802.3bt")

        command = [
            POE_PD69200_MSG_KEY_REQUEST,
            self.__calc_msg_echo(),
            POE_PD69200_MSG_SUB_CHANNEL,
            POE_PD69200_BT_MSG_SUB1_BT_LAYER2_LLDP_PSE,
            port_index,
        ]
        return self.__run_communication_protocol(
            command, self._msg_delay, PoeMsgParser.MessageType.MSG_BT_LLDP_PSE_DATA
        )

    def bt_set_port_l2_lldp_pd_request(
        self,
        port_index: int,
        power_limit_single: int,
        power_limit_mode_a: int,
        power_limit_mode_b: int,
        cable_len: int,
        priority: int = POE_PD69200_BT_MSG_DATA_PORT_PRIORITY_NO_CHANGE,
    ) -> None:
        """Set the BT TPPL for a port, as a result of an L2 power request

        Args:
            port_index (int): Port ID (0-based)
            power_limit_single (int): Requested power at PD input for
            single-signature PDs (in 0.1W)
            power_limit_mode_a (int): Requested power at PD input for
            mode A (in 0.1W)
            power_limit_mode_b (int): Requested power at PD input for
            mode B (in 0.1W)
            cable_len (int): Engineering value used to compute the cable
            resistance (0...0xA, one step means 10 meters). Setting this to 0
            will tell the controller to not compensate for the cable loss
            priority (int): Port priority
        """
        if not self._bt_support:
            raise AssertionError("The PoE chipset doesn't support 802.3bt")

        command = [
            POE_PD69200_MSG_KEY_COMMAND,
            self.__calc_msg_echo(),
            POE_PD69200_MSG_SUB_CHANNEL,
            POE_PD69200_BT_MSG_SUB1_BT_LAYER2_LLDP_PD,
            port_index,
            power_limit_single >> 8,
            power_limit_single & 0xFF,
            power_limit_mode_a >> 8,
            power_limit_mode_a & 0xFF,
            power_limit_mode_b >> 8,
            power_limit_mode_b & 0xFF,
            cable_len & 0x0F,
            priority & 0x0F,
        ]
        self.__run_communication_protocol(command, self._msg_delay)

    def bt_get_port_reserve_power_request(self, port_index):
        if not self._bt_support:
            raise AssertionError("The PoE chipset doesn't support 802.3bt")

        # as per the spec,
        # SUB1 value to set for "Get BT Port Reserve Power Request"
        # is 0x55
        # POE_PD69200_MSG_SUB1_RESET = 0x55
        command = [
            POE_PD69200_MSG_KEY_REQUEST,
            self.__calc_msg_echo(),
            POE_PD69200_MSG_SUB_CHANNEL,
            POE_PD69200_MSG_SUB1_RESET,
            port_index,
        ]
        return self.__run_communication_protocol(
            command, self._msg_delay, PoeMsgParser.MessageType.MSG_PORT_POWER_LIMIT
        )

    def bt_set_port_priority(self, port_index, priority):
        if not self._bt_support:
            raise AssertionError("The PoE chipset doesn't support 802.3bt")

        command = [
            POE_PD69200_MSG_KEY_COMMAND,
            self.__calc_msg_echo(),
            POE_PD69200_MSG_SUB_CHANNEL,
            POE_PD69200_BT_MSG_SUB1_PORT_CONFIG,
            port_index,
            POE_PD69200_BT_MSG_DATA_CMD_ENDIS_NO_CHANGE,
            POE_PD69200_BT_MSG_DATA_PORT_MODE_NO_CHANGE | POE_PD69200_BT_MSG_DATA_PORT_CLASS_ERROR_NO_CHANGE,
            POE_PD69200_BT_MSG_DATA_PORT_OP_MODE_NO_CHANGE,
            POE_PD69200_BT_MSG_DATA_PORT_NO_ADDITIONAL_POWER,
            priority,
        ]
        return self.__run_communication_protocol(command, self._msg_delay, PoeMsgParser.MessageType.MSG_CMD_STATUS)


class PoePort:

    def __init__(self, driver: PoeDriver_microsemi_pd69200, port_id: int, log_port_status: bool = False) -> None:
        self._driver: PoeDriver_microsemi_pd69200 = driver
        self._port_id: int = port_id
        self._en_dis: str = "enable"
        self._status: str = ""
        self._priority: str = ""
        self._protocol: str = ""
        self._latch: int = 0x00
        self._class_type: str = "0"
        self._FPairEn: int = 0
        self._power_consump: int = 0
        self._power_limit: int = 0
        self._voltage: int = 0
        self._current: int = 0
        # TODO: dual-signature
        self._measured_class: int = 0
        self._bt_support: bool = self._driver.bt_support
        self._log_port_status = log_port_status

    def __update_port_status(self) -> None:
        if self._bt_support:
            params = self._driver.bt_get_port_parameters(self._port_id)
            params_class = self._driver.bt_get_port_class(self._port_id)
            port_status = params.get(STATUS)
            if self._log_port_status:
                PoeLog().dbg(f"Port {self._port_id} is in status " f"0x{port_status:02X}")
            # TODO: extract conversion table as a strategy.
            self._status = TBL_BT_STATUS_TO_CFG[port_status]
            self._en_dis = TBL_ENDIS_TO_CFG[params.get(ENDIS)]
            self._measured_class = params_class[MEASURED_CLASS_ALT_A]
            # Delivers power, port status: 0x80-0x91
            if 0x80 <= port_status <= 0x91:
                if self._measured_class >= 0 and self._measured_class <= 4:
                    self._protocol = "IEEE802.3AF/AT"
                elif self._measured_class >= 5 and self._measured_class <= 8:
                    self._protocol = "IEEE802.3BT"
                else:
                    self._protocol = "N/A"
            else:
                self._protocol = "N/A"

            self._priority = TBL_PRIORITY_TO_CFG[params.get(PRIORITY)]

            power_limit = self._driver.bt_get_port_class(self._port_id)
            port_class = power_limit[ASSIGNED_CLASS_ALT_A]
            self._class_type = TBL_BT_CLASS_TO_CFG[port_class]
            self._power_limit = power_limit[TPPL]

            meas = self._driver.bt_get_port_measurements(self._port_id)
            self._current = meas.get(CURRENT)
            self._power_consump = meas.get(POWER_CONSUMP)
            self._voltage = meas.get(VOLTAGE)
        else:
            status = self._driver.get_port_status(self._port_id)
            self._en_dis = TBL_ENDIS_TO_CFG[status.get(ENDIS)]
            port_status = status.get(STATUS)
            if self._log_port_status:
                PoeLog().dbg(f"Port {self._port_id} is in status " f"0x{port_status:02X}")
            self._status = TBL_STATUS_TO_CFG[port_status]
            self._latch = status.get(LATCH)
            self._class_type = TBL_CLASS_TO_CFG[status.get(CLASS)]
            self._protocol = TBL_PROTOCOL_TO_CFG[status.get(PROTOCOL)]
            self._FPairEn = status.get(EN_4PAIR)

            priority = self._driver.get_port_priority(self._port_id)
            self._priority = TBL_PRIORITY_TO_CFG[priority.get(PRIORITY)]

            power_limit = self._driver.get_port_power_limit(self._port_id)
            self._power_limit = power_limit.get(PPL)

            meas = self._driver.get_port_measurements(self._port_id)
            self._current = meas.get(CURRENT)
            self._power_consump = meas.get(POWER_CONSUMP)
            self._voltage = meas.get(VOLTAGE)

    def get_current_status(self, more_info=True, log_status=False) -> OrderedDict:
        self.__update_port_status()
        port_status = OrderedDict()
        if self._bt_support:
            port_status[PORT_ID] = self._port_id + 1
            port_status[ENDIS] = self._en_dis
            port_status[PRIORITY] = self._priority
            port_status[POWER_LIMIT] = self._power_limit * 100
            if more_info:
                port_status[STATUS] = self._status
                port_status[PROTOCOL] = self._protocol
                port_status[LATCH] = self._latch
                port_status[EN_4PAIR] = self._FPairEn
                port_status[CLASS] = self._class_type
                port_status[POWER_CONSUMP] = self._power_consump * 100
                port_status[VOLTAGE] = self._voltage // 10
                port_status[CURRENT] = self._current
        else:
            port_status[PORT_ID] = self._port_id + 1
            port_status[ENDIS] = self._en_dis
            port_status[PRIORITY] = self._priority
            port_status[POWER_LIMIT] = self._power_limit
            if more_info:
                port_status[STATUS] = self._status
                port_status[LATCH] = self._latch
                port_status[PROTOCOL] = self._protocol
                port_status[EN_4PAIR] = self._FPairEn
                port_status[CLASS] = self._class_type
                port_status[POWER_CONSUMP] = self._power_consump
                port_status[VOLTAGE] = self._voltage // 10
                port_status[CURRENT] = self._current

        return port_status

    def set_en_dis(self, set_val, current_enDis=None, readback=False):
        status = 0
        result_get = set_val
        if (
            current_enDis is not None
            and ENDIS in current_enDis
            and self._port_id <= (len(current_enDis[ENDIS]) - 1)
            and current_enDis[ENDIS][self._port_id] == set_val
        ):
            return status
        else:
            if self._bt_support:
                result = self._driver.bt_set_port_en_dis(self._port_id, set_val)
                if readback:
                    result_get = self._driver.bt_get_port_parameters(self._port_id).get(ENDIS)
                if result == 0 or result_get == set_val:
                    status = result
            else:
                result = self._driver.set_port_en_dis(self._port_id, set_val)
                if readback:
                    result_get = self._driver.get_port_status(self._port_id).get(ENDIS)
                if result == 0 or result_get == set_val:
                    status = result

            return status

    def set_power_limit(self, set_val, readback=False):
        ret_flag = 0
        result_get = set_val
        if self._bt_support:
            # Convert from 0.1W to mW (refer to chapter 3.5.2 in the user
            # manual).
            # Note: checking if the operation was successful can be done
            # only through a readback.
            set_val //= 100
            self._driver.bt_set_port_l2_lldp_pd_request(self._port_id, set_val, 0, 0, 0)
            ret_flag = True

            if readback:
                return self._driver.bt_get_port_reserve_power_request(self._port_id).get(TPPL)
        else:
            result = self._driver.set_port_power_limit(self._port_id, set_val)
            if readback:
                result_get = self._driver.get_port_power_limit(self._port_id).get(PPL)
            if result == 0 or result_get == set_val:
                ret_flag = result
        return ret_flag

    def get_power_reserve(self):
        if self._bt_support:
            result = self._driver.get_port_power_limit(self._port_id)
            return result
        return None

    def set_priority(self, set_val, readback=False):
        ret_flag = 0
        result_get = set_val
        if self._bt_support:
            result = self._driver.bt_set_port_priority(self._port_id, set_val)
            if readback:
                result_get = self._driver.bt_get_port_parameters(self._port_id).get(PRIORITY)
            if result == 0 or result_get == set_val:
                ret_flag = result
        else:
            result = self._driver.set_port_priority(self._port_id, set_val)
            if readback:
                result_get = self._driver.get_port_priority(self._port_id).get(PRIORITY)
            if result == 0 or result_get == set_val:
                ret_flag = result
        return ret_flag

    def set_all_params(self, params: dict, current_en_dis: dict = {}, readback: bool = False) -> dict:
        """Set the port enable/disable, priority and/or power limit

        Args:
            params (dict): The port parameters
            current_enDis (dict, optional): Current ports. Defaults to {}.
            readback (bool, optional): Readback flag. Defaults to False.

        Returns:
            dict: Each operation result as a dictionary
        """
        status = {}
        if ENDIS in params:
            set_val = TBL_ENDIS_TO_DRV[params[ENDIS]]
            status[ENDIS] = self.set_en_dis(set_val, current_en_dis, readback)

        if PRIORITY in params:
            set_val = TBL_PRIORITY_TO_DRV[params[PRIORITY]]
            status[PRIORITY] = self.set_priority(set_val, readback)

        if POWER_LIMIT in params:
            set_val = params[POWER_LIMIT]
            status[POWER_LIMIT] = self.set_power_limit(set_val, readback)

        return status


class PoeSystem:
    # TODO: Extract driver interface to avoid circular import and tight
    # coupling.
    # Having this in a separate file without having a common driver interface
    # will result in a circular import.

    def __init__(self, driver: PoeDriver_microsemi_pd69200, port_count: int, power_bank_to_str: Callable[[int], str]):
        self._driver: PoeDriver_microsemi_pd69200 = driver
        self._total_ports: int = port_count
        self._power_bank_to_str: Callable[[int], str] = power_bank_to_str
        self._total_power: int = 0
        self._calculated_power: int = 0
        self._power_avail: int = 0
        self._power_bank: int = 0
        self._max_sd_volt: int = 0
        self._min_sd_volt: int = 0
        self._power_src: str = ""
        self._cpu_status1: int = 0
        self._cpu_status2: int = 0
        self._fac_default: int = 0
        self._gie: int = 0
        self._priv_label: int = 0
        self._user_byte: int = 0
        self._device_fail: int = 0
        self._temp_disco: int = 0
        self._temp_alarm: int = 0
        self._intr_reg: int = 0x00
        self._pm1: int = 0
        self._pm2: int = 0
        self._pm3: int = 0
        self._nvm_user_byte: int = 0
        self._found_device: int = 0
        self._event_exist: int = 0
        self._bt_support: bool = self._driver.bt_support

    def __update_system_status(self):
        power_params = self._driver.get_total_power()
        psu_params = self._driver.get_power_supply_params()
        self._total_power = power_params.get(POWER_LIMIT)
        self._consumed_power = power_params.get(POWER_CONSUMP)
        self._calculated_power = power_params.get(CALCULATED_POWER)
        self._power_avail = power_params.get(POWER_AVAIL)
        self._max_sd_volt = psu_params.get(MAX_SD_VOLT)
        self._min_sd_volt = psu_params.get(MIN_SD_VOLT)
        self._power_bank = power_params.get(POWER_BANK)
        self._power_src = self._power_bank_to_str(self._power_bank)
        if self._bt_support:
            system_status = self._driver.bt_get_system_status()
            self._cpu_status2 = system_status.get(CPU_STATUS2)
            self._fac_default = system_status.get(FAC_DEFAULT)
            self._priv_label = system_status.get(PRIV_LABEL)
            self._nvm_user_byte = system_status.get(NVM_USER_BYTE)
            self._found_device = system_status.get(FOUND_DEVICE)
            self._event_exist = system_status.get(EVENT_EXIST)
        else:
            system_status = self._driver.get_system_status()
            self._cpu_status1 = system_status.get(CPU_STATUS1)
            self._cpu_status2 = system_status.get(CPU_STATUS2)
            self._fac_default = system_status.get(FAC_DEFAULT)
            self._gie = system_status.get(GIE)
            self._priv_label = system_status.get(PRIV_LABEL)
            self._user_byte = system_status.get(USER_BYTE)
            self._device_fail = system_status.get(DEVICE_FAIL)
            self._temp_disco = system_status.get(TEMP_DISCO)
            self._temp_alarm = system_status.get(TEMP_ALARM)
            self._intr_reg = system_status.get(INTR_REG)

            pm_method = self._driver.get_pm_method()
            self._pm1 = pm_method.get(PM1)
            self._pm2 = pm_method.get(PM2)
            self._pm3 = pm_method.get(PM3)

    def get_current_status(self, verbose=True):
        self.__update_system_status()
        system_status = OrderedDict()
        system_status[TOTAL_PORTS] = self._total_ports
        system_status[TOTAL_POWER] = self._total_power
        system_status[POWER_CONSUMP] = self._consumed_power
        system_status[CALCULATED_POWER] = self._calculated_power
        system_status[POWER_AVAIL] = self._power_avail
        system_status[POWER_BANK] = self._power_bank
        system_status[POWER_SRC] = self._power_src
        if verbose:
            system_status[MAX_SD_VOLT] = self._max_sd_volt // 10
            system_status[MIN_SD_VOLT] = self._min_sd_volt // 10
            system_status[PM1] = self._pm1
            system_status[PM2] = self._pm2
            system_status[PM3] = self._pm3
            system_status[CPU_STATUS1] = self._cpu_status1
            # cpu status2 on AT and BT
            system_status[CPU_STATUS2] = self._cpu_status2
            system_status[FAC_DEFAULT] = self._fac_default
            system_status[GIE] = self._gie
            system_status[PRIV_LABEL] = self._priv_label
            system_status[USER_BYTE] = self._user_byte
            system_status[DEVICE_FAIL] = self._device_fail
            system_status[TEMP_DISCO] = self._temp_disco
            system_status[TEMP_ALARM] = self._temp_alarm
            system_status[INTR_REG] = self._intr_reg
            # only on BT
            system_status[NVM_USER_BYTE] = self._nvm_user_byte
            system_status[FOUND_DEVICE] = self._found_device
            system_status[EVENT_EXIST] = self._event_exist
        return system_status
