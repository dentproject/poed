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
import json
import os
import signal
import sys
import threading
import time
import traceback
from collections import OrderedDict
from concurrent import futures
from datetime import datetime, timedelta
from pathlib import Path
from shutil import copyfile
from typing import NoReturn

import grpc
import poed_ipc_pb2
import poed_ipc_pb2_grpc
from agent_constants import AgentConstants
from filelock import FileLock
from pd69200.poe_driver_def import (
    POE_PD69200_BT_MSG_DATA_LAYER2_REQ_EXECUTED,
    POE_PD69200_BT_MSG_DATA_PORT_LAYER2_USAGE_L1,
    POE_PD69200_BT_MSG_DATA_PORT_LAYER2_USAGE_LLDP,
    POE_PD69200_BT_MSG_DATA_PORT_LAYER2_USAGE_OFF,
    POE_PD69200_BT_MSG_DATA_PORT_PRIORITY_NO_CHANGE,
    POE_PD69200_MSG_DATA_PORT_LAYER2_USAGE_L1,
    TBL_BT_LAYER2_EXECUTION_TO_CFG,
)
from poe_common import *
from poe_log import PoeLog
from poe_platform import PoePlatform, PoePlatformFactory
from poe_telemetry import publish_metrics
from poe_version import POE_AGENT_VERSION, POE_CONFIG_VERSION
from singleton_thread_safe import SingletonThreadSafe
from tinyrpc.dispatch import RPCDispatcher
from tinyrpc.protocols.jsonrpc import FixedErrorMessageMixin, JSONRPCProtocol
from tinyrpc.server import RPCServer
from tinyrpc.transports.callback import CallbackServerTransport

TIME_FMT = "%Y/%m/%d %H:%M:%S"
MAX_WORKER_THREADS = 10
GRPC_STOP_NUM_SECS_TO_WAIT = 60
NS_IN_S = 1000000000

# Global thread flag used for signaling program exit.
THREAD_FLAG: bool = True
# Lock for synchronising poecli, lldp-poed, and autosave threads
PoeClientLock = threading.Lock()


class JSONRpcInvalidPortIdError(FixedErrorMessageMixin, Exception):
    jsonrpc_error_code = -42000
    message = "Invalid port ID"


class JSONRpcDriverError(FixedErrorMessageMixin, Exception):
    jsonrpc_error_code = -42100
    message = "Unexpected driver error"


class JSONRpcInvalidPowerRequestError(FixedErrorMessageMixin, Exception):
    jsonrpc_error_code = -42200
    message = "The port power request is invalid"


class JSONRpcInvalidOperationError(FixedErrorMessageMixin, Exception):
    jsonrpc_error_code = -42300
    message = "Invalid operation for the given port"


class PoeConfigDao(object):
    def __init__(self, cfg_path: str, plat_name: str, log=PoeLog()) -> None:
        self._local_cfg_path: str = cfg_path
        self._plat_name: str = plat_name
        self._log: PoeLog = log

    @staticmethod
    def is_time_sequence_increasing(t1: str, t2: str) -> bool:
        """Check whether the input timestamps are strictly increasing

        Args:
            t1 (str): First timestamp
            t2 (str): Second timestamp

        Returns:
            bool: True if the timestamps are strictly increasing, False
            otherwise
        """

        return datetime.strptime(t2, TIME_FMT) > datetime.strptime(t1, TIME_FMT)

    @property
    def local_cfg_path(self) -> str:
        return self._local_cfg_path

    @local_cfg_path.setter
    def local_cfg_path(self, local_cfg_path: str) -> None:
        """Set the local configuration file path, by first checking if the
        parent folder exists

        Args:
            local_cfg_path (str): Path to the local config
        """
        Path(local_cfg_path.rsplit("/", 1)[0]).mkdir(True, True)
        self._local_cfg_path = local_cfg_path

    def __is_valid_cfg_platform(self, cfg_plat: str) -> bool:
        """Compare the local platform name with the one read from the config
        file

        Args:
            cfg_plat (str): Platform string

        Returns:
            bool: True if the platform names match, False otherwise
        """
        return cfg_plat == self._plat_name

    def __is_valid_poe_agt_ver(self, agt_ver: str) -> bool:
        """Compare the runtime PoE agent major version with the one read from
        the local config

        Args:
            agt_ver (str): Agent version string

        Returns:
            bool: True if both major versions match, False otherwise
        """
        maj_ver_cfg = agt_ver.split(".")[0]
        maj_ver_def = POE_AGENT_VERSION.split(".")[0]
        return maj_ver_cfg == maj_ver_def

    def __is_valid_poe_cfg_ver(self, cfg_ver: str) -> bool:
        """Compare the runtime PoE config major version with the one read from
        the config file

        Args:
            cfg_ver (str): Config version string

        Returns:
            bool: True if both major versions match, False otherwise
        """
        maj_ver_cfg = cfg_ver.split(".")[0]
        maj_ver_def = POE_CONFIG_VERSION.split(".")[0]
        return maj_ver_cfg == maj_ver_def

    def __is_valid_gen_info(self, gen_info: dict) -> bool:
        """Check whether the loaded information is valid or not

        Args:
            gen_info (dict): Information regarding platform, agent and config
            versions

        Returns:
            bool: True if the information is valid, False otherwise
        """
        return (
            self.__is_valid_cfg_platform(gen_info[AgentConstants.PLATFORM])
            and self.__is_valid_poe_agt_ver(gen_info[AgentConstants.POE_AGT_VER])
            and self.__is_valid_poe_cfg_ver(gen_info[AgentConstants.POE_CFG_VER])
        )

    def __is_valid_timestamp(self, timestamp: dict) -> bool:
        """Check whether the config timestamp is valid or not

        Args:
            timestamp (dict): Config file last saved/set timestamp

        Returns:
            bool: True if the timestamps are increasing, False otherwise
        """
        last_save_time = timestamp[AgentConstants.LAST_SAVE_TIME]
        last_set_time = timestamp[AgentConstants.LAST_SET_TIME]

        return self.is_time_sequence_increasing(str(last_set_time), str(last_save_time))

    def is_config_valid(self, config: OrderedDict) -> bool:
        """Check whether the given config metadata is valid

        Args:
            config (dict): Read config

        Returns:
            bool: True if the metadata is valid, False otherwise
        """
        return self.__is_valid_gen_info(config[AgentConstants.GEN_INFO]) and self.__is_valid_timestamp(
            config[AgentConstants.TIMESTAMP]
        )

    def lazy_is_valid(self) -> bool:
        """Check if the local configuration file is valid or not
        by lazy loading

        Returns:
            bool: True if the path exists and the configuration metadata is
            valid
        """
        try:
            file_cfg = self.load()
            if Path(self.local_cfg_path).exists() and file_cfg is not None:
                return self.is_config_valid(file_cfg)
        except Exception as e:
            self._log.err("Unexpected error when reading the config from " f"{self.local_cfg_path}: {e}")

        return False

    def save(self, config: OrderedDict) -> bool:
        """Persist the input config to the local config file as JSON

        Args:
            data (OrderedDict): Config to save

        Returns:
            bool: True if successful, False otherwise
        """
        json_config = ""
        try:
            json_config = json.dumps(config, indent=4)
            with open(self.local_cfg_path, "w") as f:
                f.write(json_config)
                return True
        except IOError:
            self._log.err("Failed to persist the configuration to " f"{self.local_cfg_path}")
            self._log.dbg(json_config)

        return False

    def load(self) -> OrderedDict | None:
        """Load and parse the local configuration JSON file

        Returns:
            OrderedDict | None: Parsed config as a dictionary, if successful
        """
        try:
            with open(self.local_cfg_path, "r") as f:
                raw_json = f.read()
                if raw_json:
                    return json.loads(raw_json, object_pairs_hook=OrderedDict)
        except IOError:
            self._log.err(f"Failed to load the local configuration at " f"{self.local_cfg_path}")

        return None


class PoedServicer(poed_ipc_pb2_grpc.PoeIpcServicer):
    """
    PoedServicer is gRPC server for poed and serves all request coming from
    PoeCLI and lldp-poed. As of now it serves the requests from PoeCLI
    """

    def __init__(self, grpc_callback_handler):
        self._grpc_callback = grpc_callback_handler

    def HandlePoecli(self, request, context):
        """
        Handles the requests coming from PoeCLI
        """
        args = request.request
        poecli_reply = poed_ipc_pb2.PoecliReply()
        poecli_reply.reply = self._grpc_callback(args, "poecli")
        return poecli_reply


def respose_required_sec(delay):
    """
    Expect response in `delay` seconds.

    A call to `respose_received` is expected in less then `delay` seconds.
    If respose_received not called in time, `alarm_handler` will be
    called and the process terminated.

    :param delay: Delay to be expected in seconds.
    :rtype: None
    """
    signal.alarm(delay)


def respose_received():
    """
    Response received to confirm a `respose_required_sec` call.

    Ending with success any previous alarm.

    :rtype: None
    """
    signal.alarm(0)


class PoeAgent(object, metaclass=SingletonThreadSafe):
    """poed implementation
    The PoE daemon takes care of keeping in sync and saving/loading the PoE
    user configuration. The runtime configuration is persisted periodically to
    a local file, which can be later loaded in case of a system or agent
    restart. The user can also trigger a manual load from file or flushing the
    current settings to the PoE chipset through the CLI.
    We'll also listen for incoming requests from lldp-poed and try to honor
    them as they come, using a named pipe, as the transport mechanism, and
    JSON-RPC as the underlying protocol.

    Note: synchronized access to the PoE settings is necessary, because both
    the CLI and the daemon have write-through access to the PoE system.
    """

    def __init__(self) -> None:
        global THREAD_FLAG
        self._log: PoeLog = PoeLog()

        # First get a valid HAL and platform name.
        hal, plat_name = PoePlatformFactory.create_platform_from_bootcmd(AgentConstants.BOOTCMD_PATH)
        if hal is None or plat_name is None:
            self._log.err("Current platform is not supported or " "cannot be initialized")
            self._log.err("Poed will now exit !!!")
            poed_exit(EXIT_CODES.HAL_INIT_FAILED)
        if plat_name is None:
            raise AssertionError("Platform name cannot be empty")
        if hal is None:
            raise AssertionError("Platform HAL must not be None")
        self._plat_name: str = plat_name
        self._hal: PoePlatform = hal

        self._runtime_cfg: PoeConfigDao = PoeConfigDao(AgentConstants.POED_RUNTIME_CFG_PATH, plat_name, log=self._log)
        self._permanent_cfg: PoeConfigDao = PoeConfigDao(AgentConstants.POED_PERM_CFG_PATH, plat_name, log=self._log)

        # Cache the current ports configuration, update only if there was a
        # change. By default, LLDP processing is enabled for all the ports.
        self._ports_config: list | None = None
        self._default_power_limits: dict[int, int] = self._hal.default_power_limits

        # Config timestamp initial placeholder.
        unix_start_time = "1970/01/01 0:0:0"
        # Must be updated every time a new save is done.
        self._last_save_time = unix_start_time
        # Used for checking the updates sanity.
        self._prev_set_time = unix_start_time
        self._last_set_time = unix_start_time
        self._last_bank_type = None
        self._cfg_serial_num = 0

        # Local intervals (in seconds).
        self._autosave_wait_interval_s = 60

        self._cfg_load_max_retry = 3

        self._autosave_thread = threading.Thread(target=self.__handle_autosave)
        self._lldp_poe_thread = threading.Thread(target=self.__handle_lldp_poed)

        # Used to avoid persisting failsafe config.
        self._failsafe_flag = False

        self._rpc_dispatcher = RPCDispatcher()

        # Ensure the metrics FIFO is already created.
        if not os.path.exists(AgentConstants.POE_METRICS_FIFO_FOLDER):
            os.makedirs(AgentConstants.POE_METRICS_FIFO_FOLDER, 755)
        self.__create_fifo(AgentConstants.POE_METRICS_FIFO_PATH)

    @PoeAccessExclusiveLock
    def __get_current_bank_source(self) -> int | None:
        """Query the PoE driver to get the current power source type

        Returns:
            int | None: The source type
        """
        try:
            return self._hal.get_current_power_bank()
        except Exception as e:
            self._log.exc(f"Failed to get the system power bank: {str(e)}")

    @PoeAccessExclusiveLock
    def __get_system_running_state(self) -> dict | None:
        """Query the PoE driver to get the current system power state

        Returns:
            dict | None: The system running state
        """
        try:
            return self._hal.get_system_information(False)
        except Exception as e:
            self._log.exc(f"Failed to get the system running state: {str(e)}")

    @PoeAccessExclusiveLock
    def __get_ports_running_config(self) -> list[OrderedDict] | None:
        """Query the PoE driver to get all ports status

        Returns:
            list[OrderedDict] | None:  Ports power info
        """
        try:
            ports = list(range(self._hal.port_count()))
            return self._hal.get_ports_status(ports, False, False)
        except Exception as e:
            self._log.exc(f"Failed to get the ports running state: {str(e)}")

    def __has_psu_changes(self) -> bool:
        """Check if there is a new PSU event, by detecting a source type change

        Returns:
            bool: True if a change is detected, False otherwise
        """
        current_bank_source = self.__get_current_bank_source()
        if self._last_bank_type != current_bank_source:
            self._last_bank_type = current_bank_source
            self._log.dbg(
                "New power supply parameters: \n" + json.dumps(self._hal.get_power_supply_params(), ensure_ascii=True)
            )
            return True

        return False

    def __has_config_changes(self) -> bool:
        """Check if the configuration changed, based on the set timestamps
        Update the previous timestamp, if there is a new change.

        Returns:
            bool: True if the last set time is greater than the previous one,
            False otherwise
        """
        if PoeConfigDao.is_time_sequence_increasing(self._prev_set_time, self._last_set_time):
            self._prev_set_time = self._last_set_time
            return True

        return False

    def __has_state_changes(self) -> bool:
        """Check if there are config changes or PSU changes

        Returns:
            bool: True if detected changes, False otherwise
        """
        return self.__has_config_changes() or self.__has_psu_changes()

    def __get_current_time(self) -> str:
        """Get the current time based on the predefined format

        Returns:
            str: Current time as a string
        """
        return datetime.now().strftime(TIME_FMT)

    def __update_last_set_time(self) -> None:
        """Update the last config set timestamp with the current time
        This one may be called whenever there is a new CLI event marking that
        the PoE config was changed by the user.
        """
        with PoeClientLock:
            self._last_set_time = self.__get_current_time()

    @PoeAccessExclusiveLock
    def __init_platform(self, skip_port_init: bool = False) -> bool:
        """Initialize the PoE chipset through the HAL. This will also
        update the last set timestamp

        Args:
            skip_port_init (bool, optional): Whether to preserve the current
            port matrix or not. Defaults to False

        Returns:
            bool: True if init was successful, False otherwise
        """
        try:
            result = self._hal.init_poe(skip_port_init)
            if not has_any_op_failed(result):
                self._log.info("PoE chipset initialized successfully")
            else:
                self._log.info("Failed to initialize the PoE chipset: " f"{json.dumps(result, ensure_ascii=True)}")
                return False

            return True
        except Exception as e:
            self._log.exc(f"Failed to initialize the PoE chipset: {str(e)}")

        return False

    def __collect_general_info(self) -> OrderedDict:
        """Build a dictionary containing the platform details and the
        agent metadata (agent and config versions)

        Returns:
            OrderedDict: General info
        """
        info = OrderedDict()
        info[AgentConstants.PLATFORM] = self._plat_name
        info[AgentConstants.POE_AGT_VER] = POE_AGENT_VERSION
        info[AgentConstants.POE_CFG_VER] = POE_CONFIG_VERSION
        info[AgentConstants.CFG_SERIAL_NUM] = self._cfg_serial_num
        return info

    def __collect_running_config(self, update_time: bool = False) -> OrderedDict | None:
        """Build a dictionary containing the current running PoE config

        Returns:
            OrderedDict | None: The config, if successful
        """
        try:
            if self.__has_state_changes():
                # Update the port configuration, preserving the LLDP admin
                # endis state.
                self._log.dbg("Detected port config changes. " "Will refresh the configuration")
                with PoeClientLock:
                    if self._ports_config is None:
                        raise AssertionError("Ports config must not be None")
                    lldp_endis = {
                        port[PORT_ID]: port[AgentConstants.LLDP_ENDIS] for port in self._ports_config
                    }
                    self._ports_config = self.__get_ports_running_config()
                    if self._ports_config is None:
                        raise AssertionError("Ports config must not be None")
                    self._ports_config = [
                        {**port, AgentConstants.LLDP_ENDIS: lldp_endis[port[PORT_ID]]}
                        for port in self._ports_config
                    ]

            with PoeClientLock:
                config = OrderedDict()
                config[AgentConstants.PORT_CONFIGS] = self._ports_config
                config[AgentConstants.DEFAULT_LIMITS] = self._default_power_limits
            config[AgentConstants.GEN_INFO] = self.__collect_general_info()
            if update_time:
                config[AgentConstants.GEN_INFO][AgentConstants.CFG_SERIAL_NUM] += 1
            config[AgentConstants.TIMESTAMP] = OrderedDict(
                {
                    AgentConstants.LAST_SAVE_TIME: (self.__get_current_time() if update_time else self._last_save_time),
                    AgentConstants.LAST_SET_TIME: self._last_set_time,
                }
            )
            config[AgentConstants.SYS_INFO] = self.__get_system_running_state()
            return config
        except Exception as e:
            self._log.exc(f"Failed to collect the running configuration: {str(e)}")

        return None

    def __persist_running_config(self) -> bool:
        """Collect the PoE running configuration and persist it to the runtime
        config local file
        The configuration will be saved only if it's valid first.

        Returns:
            bool: True if successful, False otherwise
        """
        try:
            running_config = self.__collect_running_config(update_time=True)
            if running_config is None:
                raise AssertionError("Running config must not be None")
            if not self._runtime_cfg.is_config_valid(running_config):
                self._log.err("The current active config is invalid. Cannot persist it")
                self._log.dbg(f"{json.dumps(running_config, ensure_ascii=True)}")
                return False

            if self._runtime_cfg.save(running_config):
                # Update the runtime serial number and timestamp, if
                # successful, for further checks.
                self._last_save_time = running_config[AgentConstants.TIMESTAMP][AgentConstants.LAST_SAVE_TIME]
                self._cfg_serial_num = running_config[AgentConstants.GEN_INFO][AgentConstants.CFG_SERIAL_NUM]
                self._log.dbg("Successfully persisted: " f"{json.dumps(running_config, ensure_ascii=True)}")
                return True
        except Exception as e:
            self._log.exc(f"Failed to persist the running config: {str(e)}")

        return False

    def __save_config_to_permanent_file(self, out_path: str) -> None:
        """Copy the runtime configuration to the given config path
        This will ensure that all the parents are created or already exist.

        Args:
            out_path (str): Output file path
        """
        if self._runtime_cfg.lazy_is_valid():
            with PoeClientLock:
                Path(out_path).parent.mkdir(exist_ok=True, parents=True)
                copyfile(self._runtime_cfg.local_cfg_path, out_path)
        else:
            self._log.err("Runtime configuration is invalid. " "Will not save it")

    def __create_fifo(self, fifo_path: str) -> None:
        """Check if the FIFO already exists and if it's a FIFO. If it doesn't,
        create it and set read and write permissions.

        Args:
            fifo_path (str): Path to FIFO
        """
        try:
            if Path(fifo_path).exists() and not Path(fifo_path).is_fifo():
                os.remove(Path(fifo_path).as_posix())
            if not Path(fifo_path).exists():
                Path(fifo_path).parent.mkdir(exist_ok=True, parents=True)
                os.mkfifo(Path(fifo_path).as_posix())
                os.chmod(Path(fifo_path).as_posix(), 0o664)
        except Exception as e:
            self._log.exc(f"Failed to create the FIFO: {str(e)}")
            poed_exit(ret_code=EXIT_CODES.CREATE_FIFO_FAILED)

    def get_port_count(self):
        return str(self._hal.port_count())

    def get_bt_support(self):
        return "1" if self._hal._bt_support else "0"

    def get_default_power_limits(self):
        """Get the default power limits

        Returns:
            str: default power limits
        """
        with PoeClientLock:
            ret_val = json.dumps(self._default_power_limits, separators=(",", ":"))
        return ret_val

    def get_ports_lldp_endis(self, args: list):
        port_count = int(args[2])
        start, end = 3, 3 + port_count

        # If the ports were not initialized yet, return enabled
        # by default.
        with PoeClientLock:
            if self._ports_config is None:
                return ",".join([AgentConstants.ENABLE] * port_count)
        # The LLDP endis should be returned in the same order
        # as the ports were given in.
        status = args[start:end]
        status_idx, ports_idx = 0, 0
        with PoeClientLock:
            while status_idx < port_count:
                # The port range may be sparse, and in the happy scenario
                # it is contiguous as the _ports_config.
                while int(status[status_idx]) > self._ports_config[ports_idx][PORT_ID]:
                    ports_idx += 1
                status[status_idx] = self._ports_config[ports_idx][AgentConstants.LLDP_ENDIS]
                status_idx += 1
        return ",".join(status)

    @PoeAccessExclusiveLock
    def get_ports_info(self, args: list):
        # hal need ports zero-based indecis
        ports = [int(val) - 1 for val in args[3:]]
        lldp_endis = self.get_ports_lldp_endis(args).split(",")

        ports_status: list[dict] = [
            {**port, AgentConstants.LLDP_ENDIS: endis_value}
            for port, endis_value in (
                zip(self._hal.get_ports_status(ports, more_info=True, log_port_status=False), lldp_endis)
            )
        ]

        return json.dumps(ports_status, separators=(",", ":"))

    @PoeAccessExclusiveLock
    def get_versions_info(self):
        versions = OrderedDict()
        versions[SW_VERSION] = self._hal.get_poe_versions()
        versions[AgentConstants.POE_AGT_VER] = POE_AGENT_VERSION
        versions[AgentConstants.POE_CFG_VER] = POE_CONFIG_VERSION

        return json.dumps(versions, separators=(",", ":"))

    @PoeAccessExclusiveLock
    def get_system_info(self):
        """get verbose system info from hal

        Returns:
            str: System info
        """
        sys_info = self._hal.get_system_information(verbose=True)
        return json.dumps(sys_info, separators=(",", ":"))

    @PoeAccessExclusiveLock
    def get_individual_mask_registers(self):
        """Get all individual mask registers
        Refer to the "MASK Registers List" chapter for further info.

        Returns:
            str: Mask values
        """
        # This includes the 802.3bt mask keys.
        masks = list(range(0x54))
        result = OrderedDict()
        for mask in masks:
            reg_value = self._hal.get_individual_mask_regs(mask).get(ENDIS)
            result[f"0x{mask:<02x}"] = reg_value

        return json.dumps(result, separators=(",", ":"))

    @PoeAccessExclusiveLock
    def __set_port_endis(self, enable: bool, ports_detail: list):
        """Set enable/disable for the given ports

        Args:
            ports (list[int]): Ports to change
            enable (bool): True if enabling, False otherwise

        Returns:
            bool: True if successful, False otherwise
        """
        ports = [int(val) for val in ports_detail[1:]]

        try:
            for port_id in ports:
                port = self._hal.get_poe_port(port_id)
                port.set_en_dis(enable)
            return True
        except Exception as e:
            self._log.exc(f"Failed to set enable/disable: {str(e)}")

        return False

    @PoeAccessExclusiveLock
    def __set_port_priority(self, priority: int, ports_detail: list):
        """Set a new port priority for the given ports

        Args:
            ports (list[str]): Ports to change
            priority (int): New port priority

        Returns:
            bool: True if successful, False otherwise
        """
        ports = [int(val) for val in ports_detail[1:]]
        try:
            for port_id in ports:
                port = self._hal.get_poe_port(port_id)
                port.set_priority(priority)
            return True
        except Exception as e:
            self._log.exc(f"Failed to set port priority: {str(e)}")

        return False

    @PoeAccessExclusiveLock
    def __set_port_power_limit(self, power_limit: int, ports_detail: list):
        """Set a new power limit for the given ports

        Args:
            ports (list[str]): Ports to change
            limit (int): New power limit

        Returns:
            bool: True if successful, False otherwise
        """
        ports = [int(val) for val in ports_detail[1:]]
        try:
            for port_id in ports:
                port = self._hal.get_poe_port(port_id)
                port.set_power_limit(power_limit)
            return True
        except Exception as e:
            self._log.exc(f"Failed to set the power limit: {str(e)}")

        return False

    def __set_lldp_endis(self, endis_value: str, port_details: list):
        """Set a lldp enable/disable for the given ports

        Args:
            ports (list[str]): Ports to change

        Returns:
            bool: True if successful, False otherwise
        """
        start, end = 1, 1 + int(port_details[0])
        # User-facing values are one-based
        ports = [int(val) + 1 for val in port_details[start:end]]
        ports_to_change = set(map(int, ports))
        with PoeClientLock:
            for port in self._ports_config or []:
                if port[PORT_ID] in ports_to_change:
                    port[AgentConstants.LLDP_ENDIS] = endis_value
                    ports_to_change.remove(port[PORT_ID])

                if not ports_to_change:
                    break
        return True

    def __set_default_power_limit(self, args: list):
        """Set default power limit

        Args:
            args (list) : class, default value

        Returns:
            bool: True if successful, False otherwise
        """
        power_class, power_limit = int(args[2]), int(args[3])
        with PoeClientLock:
            if power_limit:
                self._default_power_limits[power_class] = power_limit
            elif power_class in self._default_power_limits and not power_limit:
                del self._default_power_limits[power_class]

        return True

    @PoeAccessExclusiveLock
    def __reset_to_factory_defaults(self):
        """Reset the chipset to the factory defaults by re-running the HAL init

        Returns:
            bool: True if successful, False otherwise
        """
        try:
            self._hal.restore_factory_defaults()
            if self._hal.init_poe(skip_port_init=False):
                self._log.notice("Successfully restored factory defaults")
                return True
        except Exception as e:
            self._log.exc(f"Failed to restore factory defaults: {str(e)}")

        return False

    @PoeAccessExclusiveLock
    def __handle_flush_settings_command(self):
        """Flush the current settings to the PoE chipset non-volatile memory."""
        try:
            self._hal.save_system_settings()
        except Exception as e:
            self._log.exc(f"Failed to flush the system settings: {str(e)}")

    def __handle_config_command(self, args: list):
        user_file = None
        try:
            _, action, user_file = args
        except ValueError:
            _, action = args

        self._log.dbg(f"Config action: {action}")
        if len(args) >= 2:
            self._log.dbg(f"Config user file: {user_file}")

        try:
            if action == AgentConstants.POECLI_SAVE_CMD:
                if user_file is None:
                    self._log.info("Saving persisted config to the " "permanent config file")
                    self.__save_config_to_permanent_file(self._permanent_cfg.local_cfg_path)
                else:
                    self._log.info(f"Saving persisted config to: {user_file}")
                    self.__save_config_to_permanent_file(user_file)
            elif action == AgentConstants.POECLI_LOAD_CMD:
                if user_file is None:
                    self._log.info("Loading config from the permanent config file")
                    self.__apply_poe_config(self._permanent_cfg)
                else:
                    self._log.info(f"Loading config from: {user_file}")
                    user_cfg = PoeConfigDao(user_file, self._plat_name, self._log)
                    self.__apply_poe_config(user_cfg)
            else:
                self._log.exc(f"Failed to handle config command: {str(e)}")
                return False
            return True
        except Exception as e:
            self._log.exc(f"Exception handleing config command: {str(e)}")

        return False

    def __handle_poecli(self, args: str):
        reply = "success"
        try:
            args = json.loads(args)

            if args[0] == AgentConstants.POECLI_GET_PORT_COUNT:
                self._log.info(f"Received a get command from poecli: {args}")
                reply = self.get_port_count()
            elif args[0] == AgentConstants.POECLI_GET_BT_SUPPORT:
                self._log.info(f"Received a get command from poecli: {args}")
                reply = self.get_bt_support()
            elif args[0] == AgentConstants.POECLI_SHOW_CMD:
                if args[1] == AgentConstants.POECLI_GET_PORTS_INFO_CMD:
                    self._log.info(f"Received a get command from poecli: {args}")
                    reply = self.get_ports_info(args)
                elif args[1] == AgentConstants.POECLI_GET_SYSTEM_INFO_CMD:
                    self._log.info(f"Received a get command from poecli: {args}")
                    reply = self.get_system_info()
                elif args[1] == AgentConstants.POECLI_GET_MASK_REGS_CMD:
                    self._log.info(f"Received a get command from poecli: {args}")
                    reply = self.get_individual_mask_registers()
                elif args[1] == AgentConstants.POECLI_GET_DEFAULT_LIMITS_CMD:
                    self._log.info(f"Received a get command from poecli: {args}")
                    reply = self.get_default_power_limits()
                elif args[1] == AgentConstants.POECLI_GET_VERSIONS_INFO_CMD:
                    self._log.info(f"Received a get command from poecli: {args}")
                    reply = self.get_versions_info()
            elif args[0] == AgentConstants.POECLI_SET_CMD:
                self._log.info(f"Received a set command from poecli: {args}")
                # The second argument is the type of the subcommand
                # and the third one is the number of ports.
                next_arg = args[1]
                if next_arg == AgentConstants.POECLI_SET_DEFAULT_LIMIT_CMD:
                    self.__set_default_power_limit(args)
                elif "ports_detail" in next_arg:
                    port_details = next_arg["ports_detail"]
                    if AgentConstants.POECLI_SET_PORT_ENDIS_CMD in next_arg:
                        self.__set_port_endis(next_arg[AgentConstants.POECLI_SET_PORT_ENDIS_CMD], port_details)
                    if AgentConstants.POECLI_SET_LLDP_ENDIS_CMD in next_arg:
                        self.__set_lldp_endis(next_arg[AgentConstants.POECLI_SET_LLDP_ENDIS_CMD], port_details)
                    if AgentConstants.POECLI_SET_PORT_PRIORITY_CMD in next_arg:
                        self.__set_port_priority(next_arg[AgentConstants.POECLI_SET_PORT_PRIORITY_CMD], port_details)
                    if AgentConstants.POECLI_SET_PORT_POWER_LIMIT_CMD in next_arg:
                        self.__set_port_power_limit(
                            next_arg[AgentConstants.POECLI_SET_PORT_POWER_LIMIT_CMD], port_details
                        )
                else:
                    self._log.err(f"Unknown poecli IPC set subcommand: {args}")
                self.__update_last_set_time()
            elif args[0] == AgentConstants.POECLI_FACTORY_RESET_CMD:
                self._log.info("Received a factory reset command " f"from poecli: {args}")
                if self.__reset_to_factory_defaults():
                    self.__update_last_set_time()
            elif args[0] == AgentConstants.POECLI_FLUSH_CMD:
                self._log.info("Received a flush settings command " f"from poecli: {args}")
                self.__handle_flush_settings_command()
            elif args[0] == AgentConstants.POECLI_CFG_CMD:
                self._log.info("Received a config command " f"from poecli: {args}")
                self.__handle_config_command(args)
            else:
                self._log.err(f"Unknown poecli IPC command: {args[0]}")
        except Exception as e:
            self._log.exc(f"An exception occurred while serving poecli request: {str(e)}")
            reply = "failed"
        return reply


    def grpc_callback_handler(self, args: str, requester: str):
        global THREAD_FLAG
        if not THREAD_FLAG:
            self._log.err(f"grpc_callback_handler ignoring request from {requester} during shutdown")
            return None

        if requester == "poecli":
            return self.__handle_poecli(args)
        else:
            self._log.err(f"grpc_callback_handler unknown handler: {requester}")

        return None


    def __get_disabled_ports(self) -> dict:
        """Return the total ports count, disabled ports indices and
        LLDP disabled ports
        This method is meant for dispatching a response through the
        JSON-RPC server.

        Returns:
            dict: response
        """
        with PoeClientLock:
            if self._ports_config is None:
                raise AssertionError("Ports config must not be None")
            return {
                "ports_total_count": self._hal.port_count(),
                # User-facing values are one-based
                "disabled_ports": (
                    [
                        port[PORT_ID]
                        for port in self._ports_config
                        if port[ENDIS] == AgentConstants.DISABLE
                    ]
                ),
                "lldp_disabled_ports": (
                    [
                        port[PORT_ID]
                        for port in self._ports_config
                        if port[AgentConstants.LLDP_ENDIS] == AgentConstants.DISABLE
                    ]
                    or None
                ),
            }

    def __validate_port_id(self, port_id: int) -> None | NoReturn:
        """Validate the port ID based on the current number of ports

        Args:
            port_id (int): Port ID being queried

        Raises:
            JSONRpcInvalidPortIdError: Raised if the port ID is invalid

        Returns:
            None | NoReturn
        """
        if port_id < 0 or port_id > (self._hal.port_count() - 1):
            raise JSONRpcInvalidPortIdError()

    def __fill_port_details(self, port_id: int) -> dict:
        """Query the port through the HAL, differentiating
        between bt and non-bt API

        Args:
            port_id (int): Port ID to query for

        Returns:
            dict: the parsed port details
        """
        if self._hal.bt_support:
            port_status = self._hal.bt_get_port_status(port_id)
            port_class_info = self._hal.bt_get_port_class(port_id)
            pd_l2_info = self._hal.bt_get_port_l2_lldp_pd_request(port_id)
            pse_l2_info = self._hal.bt_get_port_l2_lldp_pse_data(port_id)

            endis = port_status[ENDIS] == 1
            # Small catch here - if a PD is cut off (0x1F state)
            # the L2 usage field will still show as "on". Therefore,
            # must also check the TPPL.
            operational_status = (
                "on"
                if (
                    pse_l2_info[LAYER2_USAGE] != POE_PD69200_BT_MSG_DATA_PORT_LAYER2_USAGE_OFF
                    and port_class_info[TPPL]
                )
                else "off"
            )
            power_mode, assigned_class, tppl, priority = None, None, None, None
            requested_power, allocated_power = None, None
            mode_a_class, mode_b_class = None, None
            mode_a_requested, mode_b_requested = None, None
            mode_a_allocated, mode_b_allocated = None, None
            ieee_pse_power_status, ieee_pse_power_pairs = None, None
            max_power = None
            if endis:
                if pse_l2_info[LAYER2_USAGE] == POE_PD69200_BT_MSG_DATA_PORT_LAYER2_USAGE_L1:
                    power_mode = "l1"
                elif pse_l2_info[LAYER2_USAGE] == POE_PD69200_BT_MSG_DATA_PORT_LAYER2_USAGE_LLDP:
                    power_mode = "l2"
                # Assigned class will always have the mode A assigned class,
                # even if the PD is dual-signature.
                assigned_class = pse_l2_info[ASSIGNED_CLASS_ALT_A]
                tppl = port_class_info[TPPL] // 10
                # May need to determine the PSE type based on the platform
                # name and eventually a dictionary mapping it to the PSE type.
                # At the moment, it's enough to decide based on the 802.3bt
                # support.
                priority = pse_l2_info[PRIORITY]
                # These values should be equal, in case the port is already
                # reconciled or if it hasn't started L2 negotiation yet.
                requested_power = pd_l2_info[PD_REQUESTED_POWER_SINGLE]
                allocated_power = pse_l2_info[PSE_ALLOCATED_POWER_SINGLE_ALT_A]
                # TODO: Dual-signature
                mode_a_class, mode_b_class = None, None
                mode_a_requested, mode_b_requested = None, None
                mode_a_allocated, mode_b_allocated = None, None
                ieee_pse_power_status = pse_l2_info[PSE_POWERING_STATUS]
                ieee_pse_power_pairs = pse_l2_info[PSE_POWER_PAIRS_EXT]
                max_power = pse_l2_info[PSE_MAX_POWER]

            return {
                "endis": endis,
                "status": operational_status,
                "power_mode": power_mode,
                "assigned_class": assigned_class,
                "pse_type": "type_3",
                "tppl": tppl,
                "priority": priority,
                "requested_power": requested_power,
                "allocated_power": allocated_power,
                "mode_a_class": mode_a_class,
                "mode_b_class": mode_b_class,
                "mode_a_requested": mode_a_requested,
                "mode_b_requested": mode_b_requested,
                "mode_a_allocated": mode_a_allocated,
                "mode_b_allocated": mode_b_allocated,
                "ieee_pse_power_status": ieee_pse_power_status,
                "ieee_pse_power_pairs": ieee_pse_power_pairs,
                "max_power": max_power,
            }
        else:
            # At the moment of writing, all derivatives that are non-BT
            # are Type 2 PSEs. May need to be changed in the future.
            port_status = self._hal.get_port_status(port_id)
            endis = port_status[ENDIS] == 1
            operational_status = "off"
            power_mode, assigned_class, tppl, priority = None, None, None, None
            requested_power, allocated_power = None, None
            if endis:
                pse_l2_info = self._hal.get_port_l2_pse_data(port_id)
                port_power_limit = self._hal.get_port_power_limit(port_id)
                # Small catch here - if a PD is cut off (0x1F state)
                # the L2 usage field will still show as "on". Therefore,
                # must also check the TPPL.
                operational_status = (
                    "on"
                    if (
                        pse_l2_info[LAYER2_USAGE] != POE_PD69200_BT_MSG_DATA_PORT_LAYER2_USAGE_OFF
                        and port_power_limit[TPPL]
                    )
                    else "off"
                )
                if pse_l2_info[LAYER2_USAGE] == POE_PD69200_MSG_DATA_PORT_LAYER2_USAGE_L1:
                    power_mode = "l1"
                elif pse_l2_info[LAYER2_USAGE] == POE_PD69200_BT_MSG_DATA_PORT_LAYER2_USAGE_LLDP:
                    power_mode = "l2"
                if operational_status == "on":
                    assigned_class = port_status[CLASS]
                    tppl = port_power_limit[TPPL] // 1000
                    priority = pse_l2_info[PRIORITY]
                    requested_power = pse_l2_info[PD_REQUESTED_POWER]
                    allocated_power = pse_l2_info[PSE_ALLOCATED_POWER]

            return {
                "endis": endis,
                "status": operational_status,
                "power_mode": power_mode,
                "assigned_class": assigned_class,
                "pse_type": "type_2",
                "tppl": tppl,
                "priority": priority,
                "requested_power": requested_power,
                "allocated_power": allocated_power,
            }

    @PoeAccessExclusiveLock
    def __get_port_details(self, port_id: int) -> dict | NoReturn:
        """Get the current port status and, optionally, the
        802.3at and 802.3bt PoE-related fields
        This must, at a minimum, announce the current admin and operational
        state. This method is meant for dispatching a response through the
        JSON-RPC server.

        Args:
            port_id (int): Port ID to query for

        Returns:
            dict: response, if successful
        """
        port_id -= 1  # Engineering port ID is zero-based.
        self.__validate_port_id(port_id)
        with PoeClientLock:
            if self._ports_config is None:
                raise AssertionError("Ports config must not be None")
            is_lldp_enabled = [
                port[AgentConstants.LLDP_ENDIS] == AgentConstants.ENABLE
                for port in self._ports_config
                if port[PORT_ID] == port_id + 1
            ][0]

        try:
            port_details = self.__fill_port_details(port_id)

            dot3at = None
            dot3bt = None
            if port_details["pse_type"] == "type_3" and port_details["status"] == "on":
                # This means we have to fill in both dot3at and dot3bt fields.
                dot3at = {
                    "pse_type": "type_3",
                    "priority": port_details["priority"],
                    "requested_power": port_details["requested_power"],
                    "allocated_power": port_details["allocated_power"],
                }
                dot3bt = {
                    "mode_a_assigned_class": port_details["mode_a_class"],
                    "mode_b_assigned_class": port_details["mode_b_class"],
                    "mode_a_requested_power": port_details["mode_a_requested"],
                    "mode_b_requested_power": port_details["mode_b_requested"],
                    "mode_a_allocated_power": port_details["mode_a_allocated"],
                    "mode_b_allocated_power": port_details["mode_b_allocated"],
                    "pse_power_status": port_details["ieee_pse_power_status"],
                    "pse_power_pairs": port_details["ieee_pse_power_pairs"],
                    "max_power": port_details["max_power"],
                }
            elif port_details["pse_type"] == "type_2" and port_details["status"] == "on":
                dot3at = {
                    "pse_type": "type_2",
                    "priority": port_details["priority"],
                    "requested_power": port_details["requested_power"],
                    "allocated_power": port_details["allocated_power"],
                }
            elif port_details["pse_type"] != "type_2" and port_details["pse_type"] != "type_3":
                raise JSONRpcDriverError(data=f"Invalid PSE type: {port_details['pse_type']}")

            return {
                "is_admin_enabled": port_details["endis"],
                "status": port_details["status"],
                "power_mode": port_details["power_mode"],
                "assigned_class": port_details["assigned_class"],
                "tppl": port_details["tppl"],
                "is_lldp_enabled": is_lldp_enabled,
                "dot3at": dot3at,
                "dot3bt": dot3bt,
            }
        except Exception as e:
            raise JSONRpcDriverError(data=str(e))

    @PoeAccessExclusiveLock
    def __set_power_limit(
        self, port_id: int, default_power: bool, dot3at: dict | None, dot3bt: dict | None
    ) -> int | NoReturn:
        """This method can be used for either setting the default power limit,
        for requesting a port TPPL update, as a result of an LLDP power request
        or for disabling the port L2 mode. This method is meant for dispatching
        a response through the JSON-RPC server.

        Args:
            port_id (int): Port ID to query for
            default (bool): whether to set the default power limit or not
            dot3at (dict): 802.3at fields
            dot3bt (dict): 802.3bt fields

        Returns:
            int: The current port TPPL, as a result of the set operation,
            if successful
        """
        port_id -= 1  # Engineering port ID is zero-based.
        self.__validate_port_id(port_id)

        if default_power and (dot3at or dot3bt):
            raise JSONRpcInvalidOperationError(
                data="Invalid parameter combination (cannot assign default "
                "power limit and set LLDP PD request at the same time)"
            )

        power_limit = None
        if default_power:
            # This will set the default power limit.
            requested_class = (
                self._hal.bt_get_port_class(port_id)[REQUESTED_CLASS_ALT_A]
                if self._hal.bt_support
                else self._hal.get_port_status(port_id)[CLASS]
            )
            # This value is in W.
            with PoeClientLock:
                power_limit = self._default_power_limits.get(requested_class, 0)

            # Using zero cable resistance will make the controller
            # use no power loss compensation. Hence, the power limit
            # at the PD input will equal the one at PSE output.
            # For a 802.3bt PSE, we still have to use the LLDP API
            # in order to change the TPPL (changing the power reserve
            # doesn't work for lowering the power limit).
            if not power_limit:
                self._log.notice("Skipping default power limit allocation for " f"port {port_id}")
                tppl = (
                    (self._hal.bt_get_port_class(port_id)[TPPL] // 10)
                    if self._hal.bt_support
                    else (self._hal.get_port_power_limit(port_id)[TPPL] // 1000)
                )
                return tppl
            if self._hal.bt_support:
                self._hal.bt_set_port_l2_lldp_pd_request(port_id, power_limit * 10, 0, 0, 0)
            else:
                self._hal.set_port_power_limit(port_id, power_limit * 1000)
        elif not dot3at and not dot3bt:
            # This should disable the L2 port mode and set it back to L1.
            # For some reason, there is no way to do that with the 802.3bt
            # firmware (probably because there is no way to change the
            # TPPL besides executing an L2 request).
            # Hence, we'll just return the current TPPL.
            result = (
                self._hal.bt_get_port_class(port_id)[TPPL] // 10
                if self._hal.bt_support
                else self._hal.get_port_power_limit(port_id)[TPPL] // 1000
            )
            return result
        elif dot3at:
            if dot3at["requested_power"] < 0 or dot3at["requested_power"] >= 999:
                raise JSONRpcInvalidPowerRequestError(
                    data="Invalid PD requested power value " "(must be between 0 and 999)"
                )
            # This will set the PD request through the L2 controller API.
            # TODO: AS4224 and TN48M L2 neg support.
            self._hal.bt_set_port_l2_lldp_pd_request(
                port_id,
                dot3at["requested_power"],
                0,
                0,
                0,
                dot3at["priority"] or POE_PD69200_BT_MSG_DATA_PORT_PRIORITY_NO_CHANGE,
            )

        # Check if the request went through.
        result = 0
        if self._hal.bt_support:
            retry_count, retry_timeout = 3, 0.5
            pse_l2_info = {}
            for _ in range(retry_count):
                pse_l2_info = self._hal.bt_get_port_l2_lldp_pse_data(port_id)
                port_class_info = self._hal.bt_get_port_class(port_id)
                tppl: int = port_class_info[TPPL]
                if POE_PD69200_BT_MSG_DATA_LAYER2_REQ_EXECUTED == pse_l2_info[LAYER2_EXECUTION]:
                    result = tppl // 10
                    break
                time.sleep(retry_timeout)
            if not result:
                raise JSONRpcDriverError(
                    data=TBL_BT_LAYER2_EXECUTION_TO_CFG[pse_l2_info[LAYER2_EXECUTION]]
                )
        else:
            tppl = self._hal.get_port_power_limit(port_id)
            if power_limit and power_limit == tppl // 1000:
                result = power_limit
            else:
                raise JSONRpcDriverError(data="Failed to set the port TPPL for non-BT device")

        self.__update_last_set_time()
        return result

    def __handle_lldp_poed(self) -> None | NoReturn:
        """Handle the lldp_poed JSON-RPC requests

        Raises:
            SystemExit: Raised if the lldp_poed FIFO doesn't exist and cannot
            be created
        """
        global THREAD_FLAG
        read_fifo = AgentConstants.LLDP_POED_WRITE_FIFO
        write_fifo = AgentConstants.LLDP_POED_READ_FIFO

        def read_from_lldp_poed_fifo() -> bytes:
            nonlocal read_fifo
            try:
                with open(read_fifo, "r") as fifo:
                    raw_data = fifo.read()
                    return raw_data.encode("ascii")
            except Exception as e:
                self._log.exc(f"Failed to read from lldp-poed FIFO: {str(e)}")
            return bytes()

        def write_to_lldp_poed_fifo(payload: bytes) -> None:
            nonlocal write_fifo
            write_fd = None
            retry_count, retry_timeout = 2, 1
            for i in range(retry_count):
                try:
                    if i > 0:
                        self._log.info("Retrying to send the response. " f"{retry_count - i} retries remaining...")
                        time.sleep(retry_timeout)
                    write_fd = os.open(write_fifo, os.O_WRONLY | os.O_NONBLOCK)
                    break
                except OSError as e:
                    if e.errno == errno.ENXIO:
                        self._log.err("lldp-poed hasn't opened the read pipe. Cannot send back the response")
                    else:
                        self._log.exc(f"Failed to write to lldp-poed FIFO: {str(e)}")
            if write_fd:
                try:
                    os.write(write_fd, payload)
                except OSError as e:
                    if e.errno == errno.EPIPE:
                        self._log.err("lldp-poed unexpectedly closed the read pipe")
                    else:
                        self._log.exc(f"Failed to write to lldp-poed FIFO: {str(e)}")
                os.close(write_fd)

        self.__create_fifo(read_fifo)
        self.__create_fifo(write_fifo)

        # Initialize the RPC server and assign the read/write
        # local callbacks.
        self._rpc_dispatcher.add_method(self.__get_disabled_ports, "get_disabled_ports")
        self._rpc_dispatcher.add_method(self.__get_port_details, "get_port_details")
        self._rpc_dispatcher.add_method(self.__set_power_limit, "set_power_limit")
        transport = CallbackServerTransport(read_from_lldp_poed_fifo, write_to_lldp_poed_fifo)
        rpc_server = RPCServer(transport, JSONRPCProtocol(), self._rpc_dispatcher)

        # This will take care of logging all incoming and outgoing messages.
        def log_message(direction: str, context: str, message: str):
            return self._log.dbg(f"{direction} {context} {message}")

        self._log.dbg("Starting lldp_poed thread...")
        while THREAD_FLAG:
            rpc_server.receive_one_message()

        self._log.dbg("Exited lldp_poed thread")



    def __on_heartbeat_callback(self) -> None:
        """Telemetry heartbeat callback that handles the publishing of
        various metrics together with the agent heartbeat
        """
        publish_metrics("poed_heartbeat", 1)

        total_power = self._hal.get_total_power()

        self._log.dbg(f"total_power = {total_power}")
        calculated_power = total_power.get(CALCULATED_POWER)
        self._log.dbg(f"State: Calculated power: {calculated_power}W")

        power_avail = total_power.get(POWER_AVAIL)
        self._log.dbg(f"State: Available power: {power_avail}W")

        power_consump = total_power.get(POWER_CONSUMP)
        self._log.dbg(f"State: Instant power consumption: {power_consump}W")

        ports_delivering_mask: list = self._hal.get_all_ports_en_dis()[ENDIS]
        total_active_ports = ports_delivering_mask.count(1)
        self._log.dbg(f"State: Enabled port count: {total_active_ports}")

        # communicate with chipset to make this thread block if chipset is in a bad state
        # and is blocking on all calls. Blocking this thread will be caught by the watchdog
        version_reply = self.get_versions_info()
        version_reply_json = json.loads(version_reply)
        self._log.dbg(f"State: Version: {version_reply_json}")

        gie = self._hal.get_system_status2().get(GIE)
        if 0 != gie:
            self._log.err(f"State: GIE: 0x{gie:02x}")

    def __handle_autosave_slice(self) -> None:
        """Handle the work payload to be done on autosave thread on each tick."""
        try:
            respose_required_sec(60)
            self.__on_heartbeat_callback()
            if self.__persist_running_config():
                self._log.notice("Successfully autosaved the running configuration")
            else:
                self._log.err("Failed to autosave the running configuration")
        except Exception as e:
            self._log.exc(f"__handle_autosave_slice Exception: {str(e)}")
        except:
            self._log.exc(f"__handle_autosave_slice Exception.")
        finally:
            respose_received()


    def __handle_autosave(self) -> None:
        """Handle the periodic persistence of the runtime PoE config
        This will also send periodic poed heartbeats, as this thread is
        non-blocking.
        """
        global THREAD_FLAG

        self._log.dbg("Starting autosave thread...")
        while THREAD_FLAG:
            if self._failsafe_flag:
                continue

            self.__handle_autosave_slice()

            time.sleep(self._autosave_wait_interval_s)

        self._log.dbg("Exited autosave thread")

    @PoeAccessExclusiveLock
    def __flush_port_config(self, config_dao: PoeConfigDao) -> bool:
        """Flush the current port configuration to the PoE chipset
        This will load the current config through the DAO and apply it
        via the driver. Will continue to apply the configuration even if
        any operation fails.

        Args:
            config (PoeConfigDao): PoE config Data Access Object

        Returns:
            bool: True if successful, False otherwise
        """
        try:
            loaded_config = config_dao.load()
            if loaded_config is None:
                raise AssertionError("Failed to load the PoE " "configuration")

            all_port_configs = loaded_config[AgentConstants.PORT_CONFIGS]
            current_en_dis = self._hal.get_all_ports_en_dis()
            result = True
            for params in all_port_configs:
                # We expose the port as being one-indexed, but the
                # driver in fact works works with zero-based channels.
                port_id = params.get(PORT_ID) - 1
                poe_port = self._hal.get_poe_port(port_id)
                if poe_port is None:
                    raise AssertionError(f"Failed to get the PoE port: {port_id}")
                set_result = poe_port.set_all_params(params, current_en_dis, readback=False)

                if (
                    set_result[ENDIS] != 0
                    or set_result[PRIORITY] != 0
                    or (not self._hal._bt_support and set_result[POWER_LIMIT] != 0)
                ):
                    self._log.err(
                        f"Flushing Port ID {PORT_ID}] "
                        f"failed: {json.dumps(set_result, ensure_ascii=True)}"
                    )
                    result = False

            # Update the local LLDP en/dis status and the default power limits.
            with PoeClientLock:
                if self._ports_config is None:
                    raise AssertionError("Ports config must not be None")
                for local_port, loaded_port in zip(self._ports_config, all_port_configs):
                    local_port[AgentConstants.LLDP_ENDIS] = loaded_port[AgentConstants.LLDP_ENDIS]
            # The keys must be converted back to ints from JSON.
            with PoeClientLock:
                self._default_power_limits = {
                    int(power_class): limit
                    for power_class, limit in loaded_config[AgentConstants.DEFAULT_LIMITS].items()
                }

            # Must notify of the config change, otherwise the port config will
            # be stale.
            self.__update_last_set_time()
            return result
        except Exception as e:
            self._log.exc(f"Failed to flush the configuration: {str(e)}")

        return False

    def __enter_failsafe_mode(self) -> None:
        """Enter failsafe mode by disabling all PoE ports"""
        self._log.warn("Entering failsafe mode (all PoE ports are disabled)")
        self._failsafe_flag = True
        for i in range(self._hal.port_count()):
            self._hal.set_port_en_dis(i, 0)

    def __apply_poe_config(self, config: PoeConfigDao) -> bool:
        """Load the configuration through the DAO (if it's valid)
        and flush the port configuration to the PoE chipset
        This will have a limited number of retries.

        Args:
            config (PoeConfigDao): PoE config Data Access Object

        Returns:
            bool: True if successful, False otherwise
        """
        retry_count = 0
        while retry_count < self._cfg_load_max_retry:
            try:
                if not config.lazy_is_valid():
                    self._log.err("Loaded PoE configuration is invalid: " f"{config.local_cfg_path}")
                    return False

                if self.__flush_port_config(config):
                    return True
            except Exception as e:
                self._log.exc(f"Failed to apply config file from {config.local_cfg_path}" f": {str(e)}")
            retry_count += 1
            time.sleep(1)

        return False

    def init_config(self, warm_boot: bool) -> None | NoReturn:
        """Initialize the agent configuration either from the runtime or
        permanent configuration file (try to pick the runtime one if it's
        a warm boot and if it's a valid config). If there is no valid
        pre-existing configuration available, reconstruct the config file
        from the default chipset configuration.

        Args:
            warm_boot (bool): If True, the system hasn't gone through a cold
            boot yet. This means that the agent may have run previously.

        Returns:
            None | NoReturn:
        """
        try:
            respose_required_sec(60)
            # Decide between the permanent config and the previous runtime one
            # (if any).
            active_config = None
            if warm_boot and self._runtime_cfg.lazy_is_valid():
                active_config = self._runtime_cfg
            elif self._permanent_cfg.lazy_is_valid():
                active_config = self._permanent_cfg

            # Initialize the PoE chipset.
            skip_port_init = active_config is not None
            if self.__init_platform(skip_port_init):
                self._log.notice("Successfully initialized the PoE chipset")

                # It wouldn't be safe to do the read before the init.
                self._ports_config = self.__get_ports_running_config()
                if self._ports_config is None:
                    raise AssertionError("Ports config must not be None")
                for port in self._ports_config:
                    # The LLDP en/dis will be updated once the config is
                    # loaded.
                    # By default, all ports are enabled for LLDP processing.
                    port[AgentConstants.LLDP_ENDIS] = AgentConstants.ENABLE
                self._last_bank_type = self.__get_current_bank_source()
            else:
                self.__enter_failsafe_mode()
                poed_exit(ret_code=EXIT_CODES.HAL_INIT_FAILED)

            if active_config is not None:
                self._log.notice("Trying to restore PoE configuration from: " f"{active_config.local_cfg_path}")

                if self.__apply_poe_config(active_config):
                    self._log.notice("Successfully restored configuration")
                else:
                    self._log.err("Failed to restore the PoE configuration")
                    self.__enter_failsafe_mode()
            else:
                # We have to reconstruct the config from the default running
                # state.
                self._log.notice("Reconstructing the local PoE configuration from the " "current chipset state...")
                if self.__persist_running_config():
                    self._log.notice("Successfully reconstructed the configuration")
                else:
                    self._log.err("Failed to reconstruct the PoE " "configuration. Entering failsafe mode...")
                    self.__enter_failsafe_mode()
        except Exception as e:
            self._log.exc(f"Config initialization failed: {str(e)}")
            poed_exit(ret_code=EXIT_CODES.CONFIG_INIT_FAILED)
        finally:
            respose_received()

    def start(self) -> None:
        """Start the all agent threads: autosave, lldp-poed and poecli"""
        try:
            self._server = grpc.server(futures.ThreadPoolExecutor(max_workers=MAX_WORKER_THREADS))
            poed_ipc_pb2_grpc.add_PoeIpcServicer_to_server(PoedServicer(self.grpc_callback_handler), self._server)
            self._server.add_insecure_port(AgentConstants.POED_GRPC_SERVER_ADDRESS)
            self._server.start()
        except Exception as ex:
            self._log.exc(f"Poe Agent gRPC server start failed: {str(ex)}")

        self._autosave_thread.start()
        self._lldp_poe_thread.start()

    def stop(self) -> None:
        try:
            self._log.err("gRPC stop sent. Waiting for RPCs to complete...")
            self._server.stop(GRPC_STOP_NUM_SECS_TO_WAIT).wait()
            self._log.err("gRPC stop done")
        except Exception as ex:
            self._log.exc(f"Poe Agent gRPC server stop failed: {str(ex)}")

    def wait_on_agent_threads(self):
        """wait for the all agent threads: autosave, poecli and lldp_poed"""
        for thread in [self._autosave_thread, self._lldp_poe_thread]:
            if thread.is_alive():
                thread.join()


def is_process_alive(pid: int) -> bool:
    """Check if the process is still alive, given its PID

    Args:
        pid (int): Process PID

    Returns:
        bool: True if alive, False otherwise
    """
    try:
        os.kill(pid, 0)
    except OSError:
        return False

    return True


def main() -> None:
    global THREAD_FLAG

    if os.geteuid() != 0:
        raise RuntimeError("poed must be run as root")

    # A warm boot is equivalent to having the PID file present under run/
    # as this folder will get emptied on every cold boot.
    is_warm_boot = True
    try:
        prev_pid = int(open(AgentConstants.POED_PID_PATH, "r").read())
        if is_process_alive(prev_pid):
            raise SystemExit("Previous poed service is still alive." "Will not launch another instance")
    except Exception:
        # The PID file doesn't exist or the process is not alive.
        is_warm_boot = False
    finally:
        # Save our current PID.
        open(AgentConstants.POED_PID_PATH, "w").write(str(os.getpid()))

    # The initialization sequence shouldn't be interrupted by
    # any other command (e.g. CLI set command).
    poed_lock = FileLock(AgentConstants.POED_INIT_FLAG_PATH)
    with poed_lock:
        PoeAgent().init_config(is_warm_boot)

    PoeAgent().start()

    while THREAD_FLAG:
        time.sleep(1)
    print_stderr("main() exit")

def poed_exit(sig=0, frame=None, ret_code: int = 0) -> NoReturn:
    print_stderr(f"poed_exit({sig}, {frame}, {ret_code})")
    global THREAD_FLAG
    THREAD_FLAG = False
    publish_metrics("poed_exit", ret_code)
    PoeAgent().wait_on_agent_threads()
    PoeAgent().stop()
    sys.exit(ret_code)


def alarm_handler(signum, frame):
    """
    Handle an alarm call.

    :param signum: Alarm signum.
    :param frame: Alarm frame.
    :rtype: None
    """
    signame = signal.Signals(signum).name
    print_stderr(f'Signal handler called with signal {signame} ({signum})')
    print_stderr("Hang alarm raised. Restarting daemon.")
    publish_metrics("poed_exit", EXIT_CODES.HUNG_DETECTED)
    os.kill(os.getpid(), signal.SIGKILL)

if __name__ == "__main__":
    try:
        signal.signal(signal.SIGTERM, poed_exit)
        signal.signal(signal.SIGALRM, alarm_handler)
        main()
    except Exception as e:
        print_stderr(f"Unexpected error when running the daemon: {str(e)}")
    finally:
        poed_exit(EXIT_CODES.SUCCESS)
