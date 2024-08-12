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

import os
import sys
import time
import syslog
import fcntl
import traceback
from typing import Callable, ParamSpec, TypeVar

from agent_constants import AgentConstants
from poe_log import PoeLog
from tinyrpc.protocols.jsonrpc import FixedErrorMessageMixin


class EXIT_CODES:
    SUCCESS = 0
    HAL_INIT_FAILED = -1
    CONFIG_INIT_FAILED = -2
    READ_FIFO_FAILED = -3
    WRITE_FIFO_FAILED = -4
    CREATE_FIFO_FAILED = -5
    LISTEN_POECLI_EVENTS_FAILED = -6
    HUNG_DETECTED = -7

# POE Driver Attributes
TOTAL_PORTS: str = "total_ports"
TOTAL_POWER: str = "total_power"
POWER_LIMIT: str = "power_limit"
POWER_CONSUMP: str = "power_consump"
CALCULATED_POWER: str = "calculated_power"
POWER_AVAIL: str = "power_avail"
POWER_BANK: str = "power_bank"
POWER_SRC: str = "power_src"
STATUS: str = "status"
PRIORITY: str = "priority"
PORT_ID: str = "port_id"
MAX_SD_VOLT: str = "max_sd_volt"
MIN_SD_VOLT: str = "min_sd_volt"
PPL: str = "ppl"
TPPL: str = "tppl"
ENDIS: str = "enDis"
CPU_STATUS1: str = "cpu_status1"
CPU_STATUS2: str = "cpu_status2"
FAC_DEFAULT: str = "fac_def"
GIE: str = "gen_intl_err"
PRIV_LABEL: str = "priv_label"
USER_BYTE: str = "user_byte"
DEVICE_FAIL: str = "device_fail"
TEMP_DISCO: str = "temp_disc"
TEMP_ALARM: str = "temp_alarm"
INTR_REG: str = "intr_reg"
PROTOCOL: str = "protocol"
CLASS: str = "class"
VOLTAGE: str = "voltage"
CURRENT: str = "current"
CSNUM: str = "poe_dev_addr_num"
TEMP: str = "temperature"
LATCH: str = "latch"
EN_4PAIR: str = "enable_4pair"
PM1: str = "pm1"
PM2: str = "pm2"
PM3: str = "pm3"
SW_VERSION: str = "sw_version"
PROD_NUM: str = "prod_num"
CPU_STATUS2_ERROR: str = "cpu_status2_error"
NVM_USER_BYTE: str = "nvm_user_byte"
FOUND_DEVICE: str = "found_device"
EVENT_EXIST: str = "event_exist"
PORT_MODE_CFG1: str = "port_mode_cfg1"
SHUTDOWN_STATUS: str = "shutdown_status"
PORT_EVENT: str = "port_event"
ACTIVE_MATRIX_PHYA: str = "active_matrix_a"
ACTIVE_MATRIX_PHYB: str = "active_matrix_b"
OPERATION_MODE: str = "operation_mode"
PSE_ALLOCATED_POWER: str = "allocated_power"
PSE_ALLOCATED_POWER_SINGLE_ALT_A: str = "allocated_power_single_alt_a"
PSE_ALLOCATED_POWER_ALT_B: str = "allocated_power_single_alt_b"
PSE_MAX_POWER: str = "pse_max_power"
MEASURED_CLASS_ALT_A: str = "measured_class_alt_a"
MEASURED_CLASS_ALT_B: str = "measured_class_alt_b"
REQUESTED_CLASS_ALT_A: str = "requested_class_alt_a"
REQUESTED_CLASS_ALT_B: str = "requested_class_alt_b"
ASSIGNED_CLASS_ALT_A: str = "assigned_class_alt_a"
ASSIGNED_CLASS_ALT_B: str = "assigned_class_alt_b"
LAYER2_EXECUTION: str = "layer2_execution"
LAYER2_USAGE: str = "layer2_usage"
PSE_POWERING_STATUS: str = "pse_powering_status"
PSE_POWER_PAIRS_EXT: str = "pse_power_pairs_ext"
CABLE_LENGTH: str = "cable_length"
PD_REQUESTED_POWER: str = "requested_power"
PD_REQUESTED_POWER_SINGLE: str = "requested_power_single"
PD_REQUESTED_POWER_MODE_A: str = "requested_power_mode_a"
PD_REQUESTED_POWER_MODE_B: str = "requested_power_mode_b"
REQUESTED_CABLE_LENGTH: str = "requested_cable_length"
MEASURED_CLASS = "measured_class"


def print_stderr(msg: str, end: str = "\n", flush: bool = True):
    """Flush the message to stderr, when logging is not an option

    Args:
        msg (str): Message to print
        end (str, optional): Termination token. Defaults to "\n".
        flush (bool, optional): Flush flag. Defaults to True.
    """
    sys.stderr.write(msg + end)
    if flush:
        sys.stderr.flush()


def conv_byte_to_hex(bytes_in: list[int]) -> str:
    """Convert a list of byte integers into a hex-formatted string

    Args:
        byte_in (list[int]): List to convert

    Returns:
        str: Hex string
    """
    hex_string = "".join("%02x," % b for b in bytes_in)
    hex_string = hex_string + "[EOF]"
    return hex_string


_P = ParamSpec("P")  # type: ignore
_T = TypeVar("T")  # type: ignore


def PoeAccessExclusiveLock(func: Callable[_P, _T]) -> Callable[_P, _T | None]:
    """Generic function synchronization decorator

    Args:
        func (Callable[_P, _T]): Decorated function

    Returns:
        Callable[_P, _T]: Wrapper
    """

    def wrap_cmd(*args: _P.args, **kwargs: _P.kwargs) -> _T | None:
        """Execute the wrapped function only if the locking is successful.
        If the locking fails, there's a predefined number of retries
        (i.e., EXLOCK_RETRY).
        Locking is done based on a pre-defined file to allow both the PoE CLI
        and the PoE agent to have write-through access to the PoE chipset.

        Returns:
            _T | None: The decorated function return value after executing
            it or None
        """
        try:
            fd = open(AgentConstants.POE_ACCESS_LOCK_PATH, "r")
        except IOError:
            fd = open(AgentConstants.POE_ACCESS_LOCK_PATH, "wb")
        locked = False
        retry = AgentConstants.EXLOCK_RETRY
        while retry > 0:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)

                if retry < AgentConstants.EXLOCK_RETRY:
                    PoeLog().err(f"[{func.__name__}] Locked, remaining retries: " f"{str(retry)}")
                locked = True
                break
            except Exception as e:
                retry -= 1
                PoeLog().err(
                    f"[{func.__name__}] Retry locking, remaining retries: " f"{str(retry)}, exception: {str(e)}"
                )
                time.sleep(0.1)
        if locked:
            try:
                if retry < AgentConstants.EXLOCK_RETRY:
                    PoeLog().err(f"[{func.__name__}] Locked execution code")
                return func(*args, **kwargs)
            except Exception as e:
                if isinstance(e, FixedErrorMessageMixin):
                    # Tinyrpc exceptions must be propagated to allow
                    # a proper error response.
                    raise
                # Print the closest entry in the stack trace.
                error_class = e.__class__.__name__
                detail = e.args[0]
                _, _, tb = sys.exc_info()
                last_entry = traceback.extract_tb(tb)[-1]
                file_name = last_entry[0]
                line_number = last_entry[1]
                func_name = last_entry[2]
                err_message = f'File "{file_name}", line {line_number}, ' f"in {func_name}: [{error_class}] {detail}"
                PoeLog().err(f"[{func_name}] Locked, but execution failed: " f"{str(err_message)}")
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)

        return None

    return wrap_cmd


def is_active_port_matrix_different(new_matrix: list, platform_cb: Callable[[int], dict[str, int]]) -> bool:
    """Compare the actual port matrix against the current active port matrix.
    If the two matrices differ in terms of mapped physical ports, then
    return True

    Args:
        new_matrix (list): New port matrix
        platform_cb (Callable[[int], dict[str, int]])): HAL callback for
        querying the active port matrix, given a logical port index

    Returns:
        bool: True if the two matrices differ, False otherwise
    """
    if len(new_matrix[0]) == 3:
        PoeLog().info("Detected 4-Pair mode")
        four_pair = True
    else:
        PoeLog().info("Detected 2-Pair mode")
        four_pair = False

    for port_tuple in new_matrix:
        port_index = port_tuple[0]
        get_phya = platform_cb(port_index)[ACTIVE_MATRIX_PHYA]
        if get_phya != port_tuple[1]:
            PoeLog().err(
                f"Active port map logical port {port_index} PHY A is "
                "different from the new port map. Must "
                "reprogram the global matrix"
            )
            return False

        if four_pair:
            get_phyb = platform_cb(port_index)[ACTIVE_MATRIX_PHYB]
            if get_phyb != port_tuple[2]:
                PoeLog().err(
                    f"Active port map logical port {port_index} PHY B is "
                    "different from the new port map. Must "
                    "reprogram the global matrix"
                )
                return False

    PoeLog().info("Both port matrices match")
    return True


def has_any_op_failed(result: dict | list) -> bool:
    """Detect whether any command, that is part of the result dictionary,
    has failed.

    Args:
        result (dict | list): Result object, containing the operation results

    Returns:
        bool: True if any operation failed, False otherwise
    """
    if isinstance(result, dict):
        if "ret" in result:
            inner_result = result["ret"]
            if not isinstance(inner_result, int) and not isinstance(inner_result, dict):
                raise AssertionError("Invalid operation result object format")

            # Check for multiple operation results that can be
            # lumped together for the same item.
            final_result = inner_result
            if not isinstance(inner_result, int):
                final_result = 0
                for inner_name, inner_val in inner_result.items():
                    assert isinstance(inner_val, int), (
                        "Nested operation results must be passed directly "
                        "as a value to the operation key: "
                        f"{inner_name}"
                    )
                    final_result += inner_val

            if 0 != final_result:
                return True
        else:
            # Recurse into nested op result.
            for _, value in result.items():
                if has_any_op_failed(value):
                    return True
    elif isinstance(result, list):
        for op in result:
            if not isinstance(op, dict) and not isinstance(op, list):
                raise AssertionError("The operation can only be a dictionary or a list")
            if has_any_op_failed(op):
                return True

    # We're good, no command failed yet.
    return False
