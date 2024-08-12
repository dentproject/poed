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

import argparse
import errno
import getpass
import json
import os
import re
import sys
from argparse import ArgumentParser
from enum import Enum
from typing import NoReturn, OrderedDict

import grpc
import poed_ipc_pb2
import poed_ipc_pb2_grpc
from agent_constants import AgentConstants
from poe_common import *
from poe_log import PoeLog


class PoeCLI(object):
    """poecli implementation
    The PoE CLI is used by the user to apply direct changes to the PoE chipset.
    Additionally, the user can also trigger a manual save or load action
    for the config through the poed daemon by running -c --save or -c --load.
    All ports will be initially enabled to support LLDP negotiation.
    For disabling this behavior, please refer to the 'set --lldp'
    argument.
    To configure the default power limit that can be assigned for each PoE
    power class, refer to the 'set --default-limit' argument.

    Note: synchronized access to the PoE settings is necessary, because both
    the CLI and the daemon have write-through access to the PoE system.
    """

    def __init__(self) -> None:
        self._log: PoeLog = PoeLog()

        try:
            self._channel = grpc.insecure_channel(AgentConstants.POED_GRPC_SERVER_ADDRESS)
            self._stub = poed_ipc_pb2_grpc.PoeIpcStub(self._channel)
        except Exception as ex:
            self._log.exc(f"Failed to connect to gRPC server: {str(ex)}")

        self._bt_support = int(self.request_data_from_poed(json.dumps([AgentConstants.POECLI_GET_BT_SUPPORT])))
        self._port_count = int(self.request_data_from_poed(json.dumps([AgentConstants.POECLI_GET_PORT_COUNT])))

        self._parser: ArgumentParser = ArgumentParser(description="Query or change the PoE settings", prog="poecli")
        self.__build_parser()

    def request_data_from_poed(self, poecli_request: str):
        """Sends poecli request to poed gRPC server and receives the response from poed

        Args:
            poecli_request (str): User string input
        Returns:
            str : The response string returned by poed as a response
        """
        if not self.__is_poed_alive():
            raise RuntimeError("poed daemon not running. Not sending the IPC command")

        try:
            poecli_request = poed_ipc_pb2.PoecliRequest(request=poecli_request)
            poed_reply = self._stub.HandlePoecli(poecli_request)
            self._log.dbg(f"Sent poed IPC command: {poecli_request}")
            return poed_reply.reply
        except Exception as ex:
            self._log.exc(f"Failed to connect to gRPC server: {str(ex)}")
            raise

    def __parse_port_input(self, user_input: str) -> list[int] | NoReturn:
        """Validate the user port input by matching either a port range
        or a single port index

        Args:
            user_input (str): User string input

        Raises:
            argparse.ArgumentTypeError: Raised if the user input is invalid

        Returns:
            list[int] | NoReturn: The list of ports, if successful
        """
        ports = []
        port_range_regex = "^[1-9][0-9]?-[1-9][0-9]?$"
        single_port_regex = "^[1-9][0-9]?$"
        port_count = self._port_count
        try:
            targets = user_input.split(",")
            for target in targets:
                if re.match(port_range_regex, target):
                    start, end = target.split("-")
                    start = int(start)
                    end = int(end)
                    if end < start:  # Got them reversed.
                        start, end = end, start
                    if end > port_count:
                        raise ValueError
                    # Zero-based values for the driver facing API.
                    ports += list(range(start - 1, (end - 1) + 1))
                elif re.match(single_port_regex, target):
                    port = int(target)
                    if port > port_count:
                        raise ValueError
                    # Zero-based values for the driver facing API.
                    ports.append(port - 1)
                else:
                    raise ValueError
            ports = sorted(set(ports))
            return ports
        except ValueError:
            raise argparse.ArgumentTypeError(f"Invalid port input: '{user_input}'")

    def __parse_user_power_limit(self, user_input: str) -> int | NoReturn:
        """Validate the user power limit input and convert it to an integer

        Args:
            user_input (str): User string input

        Raises:
            argparse.ArgumentTypeError: Raised if the user input is invalid

        Returns:
            int | NoReturn: The converted value, if successful
        """
        try:
            power = int(user_input, 0)
            if 0 <= power <= 0xFFFF:
                return power
            else:
                raise ValueError
        except ValueError:
            raise argparse.ArgumentTypeError(f"Invalid power limit input: '{user_input}'")

    def __build_parser(self) -> None:
        """Add the subparser and arguments for the main arg parser"""
        sub_parser = self._parser.add_subparsers(dest="subcmd", help="Description", metavar="Command")

        # show sub-command
        show_parser = sub_parser.add_parser("show", help="show PoE system and port information")
        show_parser.add_argument("-d", "--debug", action="store_true", help="show verbose information")
        show_parser.add_argument("-j", "--json", action="store_true", help="dump output as JSON")
        show_exclusive_group = show_parser.add_mutually_exclusive_group(required=True)
        show_exclusive_group.add_argument(
            "-p",
            "--ports",
            metavar="<e.g. 1,3-5,10-15>",
            type=self.__parse_port_input,
            help="show PoE port(s) information",
        )
        show_exclusive_group.add_argument("-s", "--system", action="store_true", help="show PoE system information")
        show_exclusive_group.add_argument(
            "-m", "--mask", action="store_true", help="show system individual mask registers"
        )
        show_exclusive_group.add_argument(
            "--default-limits", action="store_true", help="show default class power limits"
        )
        show_exclusive_group.add_argument(
            "-a", "--all", action="store_true", help="show port, system, and individual mask registers"
        )
        show_exclusive_group.add_argument(
            "-v", "--version", action="store_true", help="show PoE firmware, agent and config versions"
        )

        # set sub-command
        set_parser = sub_parser.add_parser("set", help="change PoE configuration")
        port_group = set_parser.add_argument_group("port settings")
        port_group.add_argument(
            "-p", "--ports", metavar="<e.g. 1,3-5,10-15>", type=self.__parse_port_input, help="port index/indices"
        )
        port_group.add_argument(
            "-e", "--enable", type=int, choices=[0, 1], metavar="<e.g. 1 or 0>", help="port enable/disable"
        )
        port_group.add_argument(
            "-l",
            "--level",
            type=int,
            choices=[1, 2, 3],
            metavar="<e.g. 1 or 2 or 3>",
            help="port priority (critical = 1, high = 2, low = 3",
        )
        port_group.add_argument(
            "-o",
            "--power-limit",
            type=self.__parse_user_power_limit,
            metavar="<e.g. 20000 or 0x4e20>",
            help="port power limit (in mW)",
        )
        port_group.add_argument(
            "--lldp", type=int, choices=[0, 1], metavar="<e.g. 1 or 0>", help="lldp processing enable/disable"
        )
        limits_group = set_parser.add_argument_group("power limit settings")
        limits_group.add_argument(
            "--default-limit",
            nargs=2,
            type=int,
            metavar=("<e.g. 4 (class)>", "<e.g. 14 (0 means disable limit)>"),
            help="set the default class power limit (power class, watts)",
        )

        # flush sub-command
        sub_parser.add_parser(
            "flush",
            help="flush the current PoE configuration to the chipset "
            "non-volatile memory. Thus, these settings will become defaults "
            "after subsequent resets. To change the settings back to factory "
            "defaults, use the factory-reset poecli command",
        )

        # factory-reset sub-command
        sub_parser.add_parser(
            "factory-reset",
            help="restore the PoE chipset to factory default state. "
            "Ports will shut down after sending this command. ",
        )

        # config sub-command
        cfg_parser = sub_parser.add_parser(
            "config", help="either save the current config or load the config " "to/from a file"
        )
        config_exclusive_group = cfg_parser.add_mutually_exclusive_group(required=True)
        config_exclusive_group.add_argument(
            "-s", "--save", action="store_true", help="save persisted runtime config to a file"
        )
        config_exclusive_group.add_argument(
            "-l", "--load", action="store_true", help="load and apply config from a file"
        )
        cfg_parser.add_argument(
            "-c",
            "--config-file",
            metavar="<path>",
            help="file used for the save/load command (by default the " "permanent config file is used)",
        )

    def __get_version_info(self) -> OrderedDict:
        """Get the firmware, agent and config versions

        Returns:
            OrderedDict: Version information
        """
        poed_request = json.dumps([AgentConstants.POECLI_SHOW_CMD, AgentConstants.POECLI_GET_VERSIONS_INFO_CMD])
        poed_reply = self.request_data_from_poed(poed_request)
        version_info = json.loads(poed_reply)
        return version_info

    def __get_system_info(self) -> OrderedDict:
        """Get verbose system info

        Returns:
            OrderedDict: System info
        """
        poed_request = json.dumps([AgentConstants.POECLI_SHOW_CMD, AgentConstants.POECLI_GET_SYSTEM_INFO_CMD])
        poed_reply = self.request_data_from_poed(poed_request)
        system_info = json.loads(poed_reply)
        return system_info

    def __get_ports_info(self, ports: list[int]) -> list[dict] | NoReturn:
        """Query the PoE HAL to get verbose ports info
        The LLDP endis status must be got through poed,
        as there may be state changes that the CLI is not aware of
        through the local configuration.

        Args:
            ports (list[int]): Ports to get info for

        Returns:
            list[OrderedDict]: Ports info
        """
        poed_request = [AgentConstants.POECLI_SHOW_CMD, AgentConstants.POECLI_GET_PORTS_INFO_CMD]
        poed_request.append(str(len(ports)))
        # Ports were previously converted to zero-based,
        # because driver required zero-based indices.
        poed_request.extend(list(map(lambda p: str(p + 1), ports)))
        poed_request = json.dumps(poed_request)
        poed_reply = self.request_data_from_poed(poed_request)
        return json.loads(poed_reply)

    def __get_default_limits(self) -> OrderedDict:
        """Query the default power limits from POED

        Returns:
            list[OrderedDict]: Default power limits info
        """
        poed_request = json.dumps([AgentConstants.POECLI_SHOW_CMD, AgentConstants.POECLI_GET_DEFAULT_LIMITS_CMD])
        poed_reply = self.request_data_from_poed(poed_request)
        default_power_limits = json.loads(poed_reply)
        data = OrderedDict()
        if not default_power_limits:
            data["N/A"] = "N/A"
        else:
            data = default_power_limits
        return data

    def __get_system_individual_mask_regs(self) -> OrderedDict:
        """Get all individual mask registers
        Refer to the "MASK Registers List" chapter for further info.

        Returns:
            OrderedDict: Mask values
        """
        poed_request = json.dumps([AgentConstants.POECLI_SHOW_CMD, AgentConstants.POECLI_GET_MASK_REGS_CMD])
        poed_reply = self.request_data_from_poed(poed_request)
        mask_regs = json.loads(poed_reply)
        return mask_regs

    def __print_versions(self, versions: OrderedDict) -> None:
        """Format and display the versions

        Args:
            versions (OrderedDict): Version dictionary
        """
        print("=" * 17)
        print("PoE Versions Info")
        print("=" * 17)
        print(f" PoE firmware version : {versions[SW_VERSION]}")
        print(f" PoE agent version    : {versions[AgentConstants.POE_AGT_VER]}")
        print(f" PoE config version   : {versions[AgentConstants.POE_CFG_VER]}")

    def __print_ports_information(self, ports: list[OrderedDict], verbose: bool) -> None:
        """Format and display port(s) information

        Args:
            ports (list[OrderedDict]): Collected ports info
            verbose (bool): Verbose flag
        """
        print("")
        print("=" * 21)
        print("PoE Ports Information")
        print("=" * 21)

        # Print the table header first.
        # Some columns may be hidden, depending on the verbose arg.
        print(
            f"{'Port':<4}  {'Status':<17}  {'En/Dis':<7}  {'Priority':<8}  "
            f"{'Protocol':<14}  {'Class':<5}  {'PWR Consump':<11}  "
            f"{'PWR Limit':<11}  {'Voltage':<9}  {'Current':<8}  "
            f"{'LLDP En/Dis':<12}"
            f"{('  Latch  ') if verbose else ''}"
            f"{'En4Pair' if verbose else ''}"
        )
        print(
            f"{'-' * 4}  {'-' * 17}  {'-' * 7}  {'-' * 8}  "
            f"{'-' * 14}  {'-' * 5}  {'-' * 11}  {'-' * 11}  "
            f"{'-' * 9}  {'-' * 8}  {'-' * 12}"
            f"{('  ' + '-' * 5) if verbose else ''}"
            f"{('  ' + '-' * 7) if verbose else ''}"
        )

        # Print each port info, aligning it to each column header.
        for port in ports:
            port_id = port.get(PORT_ID)
            power_consumption = port.get(POWER_CONSUMP)
            if power_consumption is None:
                raise AssertionError(f"Power consumption value for port {port_id} must " "not be None")
            power_consumption = str(power_consumption) + " (mW)"
            power_limit = port.get(POWER_LIMIT)
            if power_limit is None:
                raise AssertionError(f"Power limit value for port {port_id} must not be None")
            power_limit = str(power_limit) + " (mW)"
            voltage = port.get(VOLTAGE)
            if voltage is None:
                raise AssertionError(f"Voltage value for port {port_id} must not be None")
            voltage = f"{voltage:.1f} (V)"
            current = port.get(CURRENT)
            if current is None:
                raise AssertionError(f"Current value for port {port_id} must not be None")
            current = str(current) + " (mA)"
            lldp_endis = port.get(AgentConstants.LLDP_ENDIS)
            if lldp_endis is None:
                raise AssertionError(f"LLDP endis value for port {port_id} must not be None")
            latch = port.get(LATCH)
            if latch is None:
                raise AssertionError(f"Latch value for port {port_id} must be not be None")
            latch = f"0x{latch:02x}"
            latch = f"  {latch:<5s}  " if verbose else ""
            en_4pair = f"{port.get(EN_4PAIR):^7d}  " if verbose else ""
            print(
                f"{port_id:<4d}  "
                f"{port.get(STATUS):<17s}  "
                f"{port.get(ENDIS):<7s}  "
                f"{port.get(PRIORITY):^8s}  "
                f"{port.get(PROTOCOL):<14s}  "
                f"{port.get(CLASS):^5s}  "
                f"{power_consumption:<11s}  {power_limit:<11s}  "
                f"{voltage:<9s}  {current:<8s}  {lldp_endis:<12s}" + latch + en_4pair
            )

    def __print_system_information_header(self):
        print("")
        print("=" * 22)
        print("PoE System Information")
        print("=" * 22)

    def __print_system_information(self, sys_info: list[OrderedDict], verbose: bool) -> None:
        """Format and display the system power information

        Args:
            sys_info (List of OrderedDict): Collected system information
            verbose (bool): Verbose flag
        """
        total_ports = 0
        total_power = 0
        consumed_power = 0

        for i in range(0, len(sys_info)):
            total_ports = total_ports + sys_info[i].get(TOTAL_PORTS)
            total_power = total_power + sys_info[i].get(TOTAL_POWER)
            consumed_power = consumed_power + sys_info[i].get(POWER_CONSUMP)

        self.__print_system_information_header()
        print(f" {'Total PoE ports':<18s}: " f"{total_ports}")
        print(f" {'Total Power':<18s}: " f"{total_power:.1f} W")
        print(f" {'Total Consumed power':<18s}: " f"{consumed_power:.1f} W")

        for i in range(0, len(sys_info)):
            self.__print_chip_system_information(sys_info[i], i, verbose, False)

    def __print_chip_system_information(self, sys_info: OrderedDict, index: int, verbose: bool, isheader:bool = False) -> None:
        """Format and display the system power information

        Args:
            sys_info (OrderedDict): Collected system information
            verbose (bool): Verbose flag
        """
        if isheader:
            self.__print_system_information_header()
        else:
            print("-" * 22)
        print(f" {'Chip index':<18s}: " f"{index}")
        print(f" {'PoE ports':<18s}: " f"{sys_info.get(TOTAL_PORTS)}")
        print(f" {'Power':<18s}: " f"{sys_info.get(TOTAL_POWER):.1f} W")
        print(f" {'Consumed power':<18s}: " f"{sys_info.get(POWER_CONSUMP):.1f} W")
        if CALCULATED_POWER in sys_info:
            print(f" {'Calculated power':<18s}: " f"{sys_info.get(CALCULATED_POWER):.1f} W")
        print(f" {'Available power':<18s}: " f"{sys_info.get(POWER_AVAIL):.1f} W")
        print("")
        print(f" {'Power bank #':<18s}: " f"{sys_info.get(POWER_BANK)}")
        print(f" {'Power sources':<18s}: " f"{sys_info.get(POWER_SRC)}")
        if verbose:
            print("=" * 26)
            print("System Status")
            print("=" * 26)
            print(f" {'Max Shutdown (V)':<18s}: " f"{sys_info.get(MAX_SD_VOLT)}")
            print(f" {'Min Shutdown (V)':<18s}: " f"{sys_info.get(MIN_SD_VOLT)}")
            print("")
            print(f" {'PM1 (system power)':<18s}: " f"0x{sys_info.get(PM1):02x}")
            print(f" {'PM2 (PPL)':<18s}: " f"0x{sys_info.get(PM2):02x}")
            print(f" {'PM3 (startup cond)':<18s}: " f"0x{sys_info.get(PM3):02x}")
            print("")
            print(f" {'CPU Status1':<18s}: " f"0x{sys_info.get(CPU_STATUS1):02x}")
            print(f" {'CPU Status2':<18s}: " f"0x{sys_info.get(CPU_STATUS2):02x}")
            print(f" {'Factory default':<18s}: " f"0x{sys_info.get(FAC_DEFAULT):02x}")
            print(f" {'General error':<18s}: " f"0x{sys_info.get(GIE):02x}")
            print(f" {'Private label':<18s}: " f"0x{sys_info.get(PRIV_LABEL):02x}")
            print(f" {'User byte':<18s}: " f"0x{sys_info.get(USER_BYTE):02x}")
            print(f" {'Device fail':<18s}: " f"0x{sys_info.get(DEVICE_FAIL):02x}")
            print(f" {'Temp disconnect':<18s}: " f"0x{sys_info.get(TEMP_DISCO):02x}")
            print(f" {'Temp alarm':<18s}: " f"0x{sys_info.get(TEMP_ALARM):02x}")
            print(f" {'Interrupt reg':<18s}: " f"0x{sys_info.get(INTR_REG):02x}")

    def __print_default_limits(self, default_power_limits: OrderedDict) -> None:
        """Formats and Print default power limits

        Args:
            default_power_limits (OrderedDict): Collected default power limits
        """
        print("")
        print("=" * 20)
        print("Default power limits")
        print("=" * 20)

        for key, value in default_power_limits.items():
            print(f" Class {key}: {value}(W)")

    def __print_mask_registers(self, masks: OrderedDict) -> None:
        """Print individual mask registers

        Args:
            masks (OrderedDict): Collected mask registers
        """
        print("")
        print("=" * 21)
        print("System mask registers")
        print("=" * 21)
        print("")

        print(f"{'-' *124}")
        length = len(masks)
        keys = []
        values = []

        for key in masks:
            keys.append(key)
            values.append(masks[key])

        index = 0
        rows_num = length // 16 + 1 if length % 16 else length // 16

        for _ in range(rows_num):
            register_row = "| Register |"
            mask_row = "| Mask     |"
            for _ in range(0, 16):
                if index < length:
                    register_row += " " + keys[index] + " |"
                    mask_row += "  " + str(values[index])
                    for _ in range(6 - len(str(values[index])) - 2):
                        mask_row += " "
                    mask_row += "|"
                else:
                    register_row += "      |"
                    mask_row += "      |"
                index += 1
            print(register_row)
            print(f"{'-' *124}")
            print(mask_row)
            print(f"{'-' *124}")

    def __show_versions(self, json_flag: bool) -> None:
        """Print the software versions

        Args:
            json_flag (bool): Dump as JSON flag
        """
        try:
            data = OrderedDict()
            data[AgentConstants.VERSIONS] = self.__get_version_info()
            if json_flag:
                print(json.dumps(data, indent=4))
            else:
                self.__print_versions(data[AgentConstants.VERSIONS])
        except Exception as e:
            self._log.exc(f"Failed to print the software versions: {str(e)}")

    def __show_system_information(self, debug_flag: bool, json_flag: bool) -> None:
        """Print the system information

        Args:
            debug_flag (bool): Verbose output flag
            json_flag (bool): Dump as JSON flag
        """
        try:
            data = OrderedDict()
            data[AgentConstants.SYS_INFO] = self.__get_system_info()
            if json_flag:
                print(json.dumps(data, indent=4))
            else:
                if type(data[AgentConstants.SYS_INFO]) is list:
                    self.__print_system_information(data[AgentConstants.SYS_INFO], debug_flag)
                else:
                    self.__print_chip_system_information(data[AgentConstants.SYS_INFO], 0, debug_flag, True)
        except Exception as e:
            self._log.exc(f"Failed to print the system information: {str(e)}")

    def __show_ports_information(self, ports: list[int], debug_flag: bool, json_flag: bool) -> None:
        """Print info for the given ports

        Args:
            ports (list[int]): Ports to query for
            debug_flag (bool): Verbose output flag
            json_flag (bool): Dump as JSON flag
        """
        try:
            data = OrderedDict()
            data[AgentConstants.PORT_INFO] = self.__get_ports_info(ports)
            if json_flag:
                print(json.dumps(data, indent=4))
            else:
                self.__print_ports_information(data[AgentConstants.PORT_INFO], debug_flag)
        except Exception as e:
            self._log.exc(f"Failed to print the ports information: {str(e)}")

    def __show_individual_mask_regs(self, json_flag: bool) -> None:
        """Print the individual mask registers

        Args:
            json_flag (bool): Dump as JSON flag
        """
        try:
            data = OrderedDict()
            data[AgentConstants.REG_MASKS] = self.__get_system_individual_mask_regs()
            if json_flag:
                print(json.dumps(data, indent=4))
            else:
                self.__print_mask_registers(data[AgentConstants.REG_MASKS])
        except Exception as e:
            self._log.exc(f"Failed to print the individual registers: {str(e)}")

    def __show_default_power_limits(self, json_flag: bool) -> None | NoReturn:
        """Print the default class power limits

        Args:
            json_flag (bool): Dump as JSON flag
        """
        try:
            data = OrderedDict()
            data[AgentConstants.DEFAULT_LIMITS] = self.__get_default_limits()
            if json_flag:
                print(json.dumps(data, indent=4))
            else:
                self.__print_default_limits(data[AgentConstants.DEFAULT_LIMITS])
        except Exception as e:
            self._log.exc(f"Failed to print the default power limits: {str(e)}")

    def __show_all_information(self, debug_flag: bool, json_flag: bool) -> None:
        """Print all information regarding versions, system, ports
        and mask registers

        Args:
            debug_flag (bool): Verbose output flag
            json_flag (bool): Dump as JSON flag
        """
        try:
            port_count = self._port_count
            ports = list(range(port_count))
            if json_flag:
                data = OrderedDict()
                data[AgentConstants.VERSIONS] = self.__get_version_info()
                data[AgentConstants.SYS_INFO] = self.__get_system_info()
                data[AgentConstants.PORT_INFO] = self.__get_ports_info(ports)
                data[AgentConstants.DEFAULT_LIMITS] = self.__get_default_limits()
                data[AgentConstants.REG_MASKS] = self.__get_system_individual_mask_regs()
                print(json.dumps(data, indent=4))
            else:
                self.__show_versions(False)
                self.__show_system_information(debug_flag, False)
                self.__show_ports_information(ports, debug_flag, False)
                self.__show_default_power_limits(False)
                self.__show_individual_mask_regs(False)
        except Exception as e:
            self._log.exc(f"Failed to print all PoE information: {str(e)}")

    def __is_poed_alive(self) -> bool:
        """Determine whether the PoE agent is still alive
        through the PID file

        Returns:
            bool: True if still alive, False otherwise
        """
        try:
            pid = int(open(AgentConstants.POED_PID_PATH, "r").read())
            os.kill(pid, 0)
        except OSError:
            return False

        return True

    def __log_current_command(self) -> None:
        """Log the current user command, TTY, working directory and user"""
        if sys.stdin.isatty():
            tty = os.ttyname(sys.stdin.fileno())
        else:
            tty = "unknown"
        current_dir = os.getcwd()
        user = getpass.getuser()
        command = " ".join(sys.argv)

        self._log.dbg(f"Command executed: TTY={tty}; WD={current_dir}; " f"USER={user}; COMMAND={command}")

    def __set_handle_config_args(self, args, action_args):
        action_args.append(AgentConstants.POECLI_CFG_CMD)
        if args.save:
            action_args.append(AgentConstants.POECLI_SAVE_CMD)
        elif args.load:
            action_args.append(AgentConstants.POECLI_LOAD_CMD)

        # Append the config file path, if given.
        if args.config_file is not None:
            action_args.append(args.config_file)

    def __set_default_limit_args(self, args, action_args):
        """Change the default power limit by adding the class
        and its limit to the action args to be processed by poed

        Args:
            args (list): user Input list
            action_args (list): IPC arguments list
        """
        action_args.append(AgentConstants.POECLI_SET_DEFAULT_LIMIT_CMD)
        action_args.append(args.default_limit[0])
        action_args.append(args.default_limit[1])

    def execute(self) -> None | NoReturn:
        """Run the main logic for executing a user command"""

        class CmdAction(Enum):
            SHOW_PORT_CONFIG = 1
            SET_PORT_CONFIG = 2
            SET_LLDP_ENDIS = 3
            SET_DEFAULT_LIMIT = 4
            SET_CONFIG = 5

        args = self._parser.parse_args()
        self.__log_current_command()
        action_args = []
        if args.subcmd == "show":
            debug_flag: bool = args.debug
            json_flag: bool = args.json
            if args.ports:
                self.__show_ports_information(args.ports, debug_flag, json_flag)
            elif args.system:
                self.__show_system_information(debug_flag, json_flag)
            elif args.mask:
                self.__show_individual_mask_regs(json_flag)
            elif args.default_limits:
                self.__show_default_power_limits(json_flag)
            elif args.all:
                self.__show_all_information(debug_flag, json_flag)
            elif args.version:
                self.__show_versions(json_flag)
        elif args.subcmd == "set":
            action_args.append(AgentConstants.POECLI_SET_CMD)
            if args.ports is not None and args.default_limit is not None:
                self._parser.error("Must not change port configuration and default power " "limits at the same time")
            if args.ports:
                if args.enable is None and args.level is None and args.power_limit is None and args.lldp is None:
                    self._parser.error(f"No action requested for {args.subcmd} command")

                action_details = OrderedDict()
                # ports are zero-based indices here
                ports_detail = [str(len(args.ports))]
                ports_detail.extend(list(map(lambda p: str(p), args.ports)))
                action_details["ports_detail"] = ports_detail
                if args.enable is not None:
                    action_details[AgentConstants.POECLI_SET_PORT_ENDIS_CMD] = args.enable
                if args.level is not None:
                    action_details[AgentConstants.POECLI_SET_PORT_PRIORITY_CMD] = args.level
                if args.power_limit is not None:
                    action_details[AgentConstants.POECLI_SET_PORT_POWER_LIMIT_CMD] = args.power_limit
                if args.lldp is not None:
                    action_details[AgentConstants.POECLI_SET_LLDP_ENDIS_CMD] = (
                        AgentConstants.ENABLE if args.lldp else AgentConstants.DISABLE
                    )
                action_args.append(action_details)
            elif args.default_limit:
                # 60W is the maximum power limit for a Type 3 PSE.
                # Supported PoE classes range from 1 to 4 (802.3af and at)
                # and from 1 to 6 (802.3bt).
                power_class, power_limit = (args.default_limit[0], args.default_limit[1])
                if self.bt_support and (args.default_limit[0] > 6 or args.default_limit[1] > 60):
                    self._parser.error("Invalid power class or value")
                elif not self.bt_support and (args.default_limit[0] > 4 or args.default_limit[1] > 30):
                    self._parser.error("Invalid power class or value")

                self.__set_default_limit_args(args, action_args)
        elif args.subcmd == "flush":
            action_args.append(AgentConstants.POECLI_FLUSH_CMD)
        elif args.subcmd == "factory-reset":
            action_args.append(AgentConstants.POECLI_FACTORY_RESET_CMD)
        elif args.subcmd == "config":
            self.__set_handle_config_args(args, action_args)

        # Notify poed of the set operation, if the command went
        # through.
        if args.subcmd != "show":
            poecli_request = json.dumps(action_args)
            try:
                reply = self.request_data_from_poed(poecli_request)
                if reply == "success":
                    print("command excecuted successfully")
            except Exception as e:
                print(f"Command failed with exception: {e}")


def main() -> None:
    try:
        cli = PoeCLI()
        cli.execute()
    except Exception as e:
        print(f"PoeCLI failed with exception: {e}")

if __name__ == "__main__":
    main()
