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
from pathlib import Path

# POE Driver Attributes
TOTAL_PORTS   = "total_ports"
TOTAL_POWER   = "total_power"
POWER_LIMIT   = "power_limit"
POWER_CONSUMP = "power_consump"
POWER_AVAIL   = "power_avail"
POWER_BANK    = "power_bank"
POWER_SRC     = "power_src"
STATUS        = "status"
PRIORITY      = "priority"
PORT_ID       = "port_id"
MAX_SD_VOLT   = "max_sd_volt"
MIN_SD_VOLT   = "min_sd_volt"
PPL           = "ppl"
TPPL          = "tppl"
ENDIS         = "enDis"
CPU_STATUS1   = "cpu_status1"
CPU_STATUS2   = "cpu_status2"
FAC_DEFAULT   = "fac_def"
GIE           = "gen_intl_err"
PRIV_LABEL    = "priv_label"
USER_BYTE     = "user_byte"
DEVICE_FAIL   = "device_fail"
TEMP_DISCO    = "temp_disc"
TEMP_ALARM    = "temp_alarm"
INTR_REG      = "intr_reg"
PROTOCOL      = "protocol"
CLASS         = "class"
VOLTAGE       = "voltage"
CURRENT       = "current"
CSNUM         = "poe_dev_addr_num"
TEMP          = "temperature"
LATCH         = "latch"
EN_4PAIR      = "enable_4pair"
PM1           = "pm1"
PM2           = "pm2"
PM3           = "pm3"
SW_VERSION    = "sw_version"
PROD_NUM      = "prod_num"
CPU_STATUS2_ERROR = "cpu_status2_error"
NVM_USER_BYTE = "nvm_user_byte"
FOUND_DEVICE = "found_device"
EVENT_EXIST = "event_exist"
# POE Configuration Attributes
GEN_INFO       = "GENERAL_INFORMATION"
TIMESTAMP      = "TIMESTAMP"
SYS_INFO       = "SYSTEM_INFORMATION"
PORT_CONFIGS   = "PORTS_CONFIGURATIONS"
PORT_INFO      = "PORT_INFORMATION"
INDV_MASKS     = "INDV_MASKS"
VERSIONS       = "VERSIONS"
PLATFORM       = "platform"
POE_AGT_VER    = "poe_agent_version"
POE_CFG_VER    = "poe_config_version"
CFG_SERIAL_NUM = "file_serial_number"
LAST_SAVE_TIME = "file_save_time"
LAST_SET_TIME  = "last_poe_set_time"
OPERATION_MODE = "operation_mode"
MEASURED_CLASS = "measured_class"
CMD_EXECUTE_RESULT = "CMD_EXECUTE_RESULT"
ACTIVE_MATRIX_PHYA = "ACTIVE_MATRIX_A"
ACTIVE_MATRIX_PHYB = "ACTIVE_MATRIX_B"


# IPC EVENT
POE_IPC_EVT    = "/run/poe_ipc_event"
POECLI_SET     = "poecli_set"
POECLI_CFG     = "poecli_cfg"



#POED CFG Predefine
POED_PERM_CFG_PATH    = "/etc/poe_agent/poe_perm_cfg.json"
POED_RUNTIME_CFG_PATH = "/run/poe_runtime_cfg.json"
POED_SAVE_ACTION = "save"
POED_LOAD_ACTION = "load"

# POE Access Exclusive Lock
POE_ACCESS_LOCK = "/run/poe_access.lock"
EXLOCK_RETRY = 5

# POE PID file location
POED_PID_PATH   = "/run/poed.pid"

# POE fileflag function
POED_BUSY_FLAG = "/run/.poed_busy"
POED_EXIT_FLAG = "/run/.poed_exit"
FILEFLAG_RETRY = 5

def print_stderr(msg,end="\n",flush=True):
    sys.stderr.write(msg+end)
    if flush:
        sys.stderr.flush()

class PoeLog(object):
    def __init__(self, debug_mode=False):
        self.debug_mode = debug_mode

    def emerg(self, msg):
        self._record(syslog.LOG_EMERG, "EMERG: %s" % msg)

    def alert(self, msg):
        self._record(syslog.LOG_ALERT, "ALERT: %s" % msg)

    def crit(self, msg):
        self._record(syslog.LOG_CRIT, "CRIT: %s" % msg)

    def err(self, msg):
        self._record(syslog.LOG_ERR, "ERR: %s" % msg)

    def warn(self, msg):
        self._record(syslog.LOG_WARNING, "WARN: %s" % msg)

    def notice(self, msg):
        self._record(syslog.LOG_NOTICE, "NOTICE: %s" % msg)

    def info(self, msg):
        self._record(syslog.LOG_INFO, "INFO: %s" % msg)

    def dbg(self, msg):
        self._record(syslog.LOG_DEBUG, "DBG: %s" % msg)

    def _record(self, priority, msg):
        syslog.syslog(priority, msg)
        if self.debug_mode == True:
            sys.stdout.write(msg+"\n")

def PoeAccessExclusiveLock(func):
    def wrap_cmd(*args, **kwargs):
        try:
            fd = open(POE_ACCESS_LOCK, 'r')
        except IOError:
            fd = open(POE_ACCESS_LOCK, 'wb')
        res = False
        LOCKED = False
        retry = EXLOCK_RETRY
        while retry > 0:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                print_stderr("[{0}]Locked, retry: {1}".format(
                    func.__name__, str(retry)))
                LOCKED = True
                break
            except Exception as e:
                # pass
                retry = retry-1
                print_stderr("[{0}]Retry locking, retry: {1}, Exception: {2}".format(
                    func.__name__, str(retry),str(e)))
                time.sleep(0.1)
                if retry == 0:
                    return res
        if LOCKED:
            try:
                print_stderr("[{0}]Locked execution code".format(
                    func.__name__))
                res = func(*args, **kwargs)
            except Exception as e:
                print_stderr("[{0}]Locked but execution failed: {1}".format(
                    func.__name__, str(e)))
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
        return res
    return wrap_cmd


def touch_file(file_path):
    try:
        return Path(file_path).touch()
    except Exception as e:
        print_stderr("Fail to touch: "+file_path+",err: "+str(e))
        return False


def remove_file(file_path):
    try:
        if check_file(file_path):
            return Path(file_path).unlink()
        else:
            return True
    except Exception as e:
        print_stderr("Fail to remove: "+file_path+",err: "+str(e))
        return False


def check_file(file_path):
    try:
        return Path(file_path).exists()
    except Exception as e:
        print_stderr("Fail to check: "+file_path+",err: "+str(e))
        return False


def wait_poed_busy(timeout=FILEFLAG_RETRY):
    ret = check_file(POED_BUSY_FLAG)
    while ret == True:
        ret = check_file(POED_BUSY_FLAG)
        print_stderr("\rpoe agent busy...")
        if timeout > 0:
            timeout -= 1
        else:
            print_stderr("\r\rpoe agent busy...timeout")
            return False
        time.sleep(1)
    return True


def conv_byte_to_hex(byte_in):
    hex_string = "".join("%02x," % b for b in byte_in)
    hex_string = hex_string+"[EOF]"
    return hex_string

def fast_temp_matrix_compare(def_matrix,plat_obj):
    get_phya = None
    get_phyb = None
    if len(def_matrix[0]) == 3:
        print_stderr("Select 4-Pair mode")
        four_pair = True
    else:
        print_stderr("Select 2-Pair mode")
        four_pair = False
    for def_mat_pair in def_matrix:
        idx = def_mat_pair[0]
        get_phya = plat_obj.get_active_matrix(idx)[ACTIVE_MATRIX_PHYA]
        if get_phya != def_mat_pair[1]:
            print_stderr("Port map mismatch, run program global matrix")
            return False
        if four_pair == True:
            get_phyb = plat_obj.get_active_matrix(idx)[ACTIVE_MATRIX_PHYB]
            if get_phyb != def_mat_pair[2]:
                print_stderr("Port map mismatch, run program global matrix")
                return False
    print_stderr("Port map match, skip program global matrix")
    return True
