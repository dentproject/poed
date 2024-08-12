'''
Copyright Amazon Inc.

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


from pathlib import Path

class AgentConstants:
    """Agent config metadata string mapping"""

    BOOTCMD_PATH: str = "/proc/cmdline"
    ONL_PLATFORM_PATH: str = "/etc/onl/platform"
    PLAT_VENDOR_PATH: str = Path.cwd().parent.joinpath("platforms").as_posix()
    GEN_INFO: str = "GENERAL_INFORMATION"
    TIMESTAMP: str = "TIMESTAMP"
    SYS_INFO: str = "SYSTEM_INFORMATION"
    PORT_CONFIGS: str = "PORTS_CONFIG"
    DEFAULT_LIMITS: str = "DEFAULT_POWER_LIMITS"
    LLDP_ENDIS: str = "lldpEnDis"
    ENABLE: str = "enable"
    DISABLE: str = "disable"
    PORT_INFO: str = "PORTS_INFORMATION"
    REG_MASKS: str = "REG_MASKS"
    VERSIONS: str = "VERSIONS"
    PLATFORM: str = "platform"
    POE_AGT_VER: str = "poe_agent_version"
    POE_CFG_VER: str = "poe_config_version"
    CFG_SERIAL_NUM: str = "file_serial_number"
    LAST_SAVE_TIME: str = "file_save_time"
    LAST_SET_TIME: str = "last_updated"
    CMD_RESULT_RET: str = "ret"
    POE_CPLD_RESET_RQ_PATH: str = "/run/.poed_cpld_reset"

    # Track global chipset state, like non-overlapping echo bytes.
    POE_COMM_STATE_PATH: str = "/run/poe_comm_state.json"

    # poed persistence related.
    POED_PERM_CFG_PATH: str = "/etc/poe_agent/poe_perm_cfg.json"
    POED_RUNTIME_CFG_PATH: str = "/run/poe_runtime_cfg.json"
    POED_PID_PATH: str = "/run/poed.pid"
    POE_ACCESS_LOCK_PATH: str = "/run/poe_access.lock"
    EXLOCK_RETRY: int = 5

    # File flag, indicating the resource status.
    POE_BUSY_FLAG_PATH: str = "/run/poe_busy.lock"
    POED_INIT_FLAG_PATH: str = "/run/poed_init.lock"
    POED_EXIT_FLAG_PATH: str = "/run/.poed_exit"
    FILEFLAG_RETRY: int = 5

    # poecli interop.
    POECLI_GET_PORT_COUNT: str = "poecli_get_port_count"
    POECLI_GET_BT_SUPPORT: str = "poecli_get_bt_support"
    POECLI_SHOW_CMD: str = "poecli_show"
    POECLI_GET_PORTS_INFO_CMD: str = "poecli_get_ports_info"
    POECLI_GET_SYSTEM_INFO_CMD: str = "poecli_get_system_info"
    POECLI_GET_VERSIONS_INFO_CMD: str = "poecli_get_versions_info"
    POECLI_GET_MASK_REGS_CMD: str = "poecli_get_mask_regs"
    POECLI_GET_LLDP_ENDIS_CMD: str = "poecli_get_lldp_endis"
    POECLI_GET_DEFAULT_LIMITS_CMD: str = "poecli_get_default_limits"
    POECLI_SET_CMD: str = "poecli_set"
    POECLI_SET_PORT_ENDIS_CMD: str = "poecli_set_port_endis"
    POECLI_SET_LLDP_ENDIS_CMD: str = "poecli_set_lldp_endis"
    POECLI_SET_DEFAULT_LIMIT_CMD: str = "poecli_set_default_pwr"
    POECLI_SET_PORT_PRIORITY_CMD: str = "poecli_set_port_priority"
    POECLI_SET_PORT_POWER_LIMIT_CMD: str = "poecli_set_port_power_limit"
    POECLI_FACTORY_RESET_CMD: str = "poecli_factory_reset"
    POECLI_FLUSH_CMD: str = "poecli_flush"
    POECLI_CFG_CMD: str = "poecli_cfg"
    POECLI_SAVE_CMD: str = "poecli_save"
    POECLI_LOAD_CMD: str = "poecli_load"

    POE_METRICS_FIFO_FOLDER: str = "/run/poe_helper/"
    POE_METRICS_FIFO_PATH: str = "/run/poe_helper/poe_metrics_fifo"

    LLDP_POED_READ_FIFO: str = "/run/lldp_poed_read"
    LLDP_POED_WRITE_FIFO: str = "/run/lldp_poed_write"

    POED_GRPC_SERVER_ADDRESS: str = "localhost:5005"
