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
from datetime import datetime, date
from collections import OrderedDict
from shutil import copyfile
from poe_common import *
from poe_version import *

from pathlib import Path
import os
import sys
import errno
import threading
import signal
import imp
import time
import json
import fcntl
import binascii

bootcmd_path   = "/proc/cmdline"
pa_root_path   = os.getcwd() + "/../"
plat_root_path = pa_root_path + "platforms"

TIME_FMT       = "%Y/%m/%d %H:%M:%S"

thread_flag    = True

class PoeAgentState(object):
    CLEAN_START = 0
    UNCLEAN_START = 1

class PoeConfig(object):
    def __init__(self, cfg_path, plat_name):
        self._path = cfg_path
        self.plat_name = plat_name
        self.root_path = self.path().rsplit("/", 1)[0]
        self.create_dir(self.root_path)

    def path(self):
        return self._path

    def create_dir(self, path):
        if self.is_exist(path) != True:
            os.mkdir(path)

    def is_exist(self, path=None):
        if path is None:
            path = self.path()
        return os.path.exists(path)

    def is_valid_cfg_platform(self, cfg_plat):
        return cfg_plat == self.plat_name

    def is_valid_poe_agt_ver(self, agt_ver):
        maj_ver_cfg = agt_ver.split('.')[0]
        maj_ver_def = POE_AGENT_VERSION.split('.')[0]
        return maj_ver_cfg == maj_ver_def

    def is_valid_poe_cfg_ver(self, cfg_ver):
        maj_ver_cfg = cfg_ver.split('.')[0]
        maj_ver_def = POE_CONFIG_VERSION.split('.')[0]
        return maj_ver_cfg == maj_ver_def

    def is_valid_gen_info(self, gen_info):
        return self.is_valid_cfg_platform(gen_info[PLATFORM]) and \
               self.is_valid_poe_agt_ver(gen_info[POE_AGT_VER]) and \
               self.is_valid_poe_cfg_ver(gen_info[POE_CFG_VER])

    def is_increasing_time_sequence(self, t1, t2):
        tDelta = datetime.strptime(t2, TIME_FMT) - \
                 datetime.strptime(t1, TIME_FMT)
        return (tDelta.days > 0 or tDelta.seconds > 0) and \
               (tDelta.days * tDelta.seconds) >= 0

    def is_valid_timestamp(self, timestamp):
        last_save_time = timestamp[LAST_SAVE_TIME]
        last_set_time = timestamp[LAST_SET_TIME]
        return self.is_increasing_time_sequence(last_set_time, last_save_time)

    def is_valid_data(self, data):
        return self.is_valid_gen_info(data[GEN_INFO]) and \
               self.is_valid_timestamp(data[TIMESTAMP])

    def is_valid(self):
        return self.is_exist() and self.is_valid_data(self.load())

    def save(self, data):
        json_data = json.dumps(data, indent = 4)
        with open(self.path(), 'w') as f:
            f.write(json_data)
            return True
        return False

    def load(self):
        with open(self.path(), 'r') as f:
            read_buf = f.read()
            if len(read_buf) > 1:
                return json.loads(read_buf)
        return None

class PoeAgent(object):
    UNIX_START_TIME = "1970/01/01 0:0:0"

    def __init__(self):
        self.log = PoeLog()
        self.plat_name = self.platform_model()
        self.poe_plat = self.load_poe_plat()
        self.plat_supported = self.is_valid_plat(self.poe_plat)
        self.poe_agent_state = PoeAgentState.CLEAN_START

        self.system_state = None
        self.all_port_state = None
        self.last_cfg_save_time = self.UNIX_START_TIME
        self.prev_poe_set_time = self.UNIX_START_TIME
        self.last_poe_set_time = self.UNIX_START_TIME
        self.last_power_bank = 0
        self.cfg_serial_num = 0

        self.runtime_cfg = PoeConfig(POED_RUNTIME_CFG_PATH,
                                     self.plat_name)
        self.permanent_cfg = PoeConfig(POED_PERM_CFG_PATH,
                                       self.plat_name)
        self.cfg_update_intvl_rt = 4
        self.cfg_update_intvl_perm = 30
        self.cfg_load_retry = 3
        self.rt_counter = 0
        self.fail_counter = 0
        self.autosave_intvl = 1
        self.autosave_thread = threading.Thread(target=self.autosave_main)

    # Get platform model from boot cmd
    def platform_model(self, file_path=bootcmd_path):
        try:
            with open(file_path, 'r') as f:
                d = dict(i.split('=') for i in f.read().split(' '))
                return d.get("onl_platform").rstrip()
        except Exception as e:
            self.log.alert("Failed to get model name. err: %s" % str(e))
            return "Unknown"

    def platform_src_path(self):
        try:
            # dentOS platform format: <arch>-<manufacturer>-<model>-<revision>
            [arch, manufacturer, model_revision] = self.plat_name.split('-', 2)
            return "/".join([plat_root_path, manufacturer,
                             model_revision, "poe_platform.py"])
        except Exception as e:
            self.log.alert("Failed to get platform path. err: %s" % str(e))
            return ""

    def load_poe_plat(self):
        poe_plat = None
        try:
            plat_src = imp.load_source("poe_plat", self.platform_src_path())
            poe_plat = plat_src.get_poe_platform()
        except Exception as e:
            self.log.alert("Failed to load PoE platform. err: %s" % str(e))
        return poe_plat

    def is_valid_plat(self, poe_plat):
        return poe_plat is not None

    def have_set_event(self):
        if self.runtime_cfg.is_increasing_time_sequence(self.prev_poe_set_time,
                                                        self.last_poe_set_time):
            self.prev_poe_set_time = self.last_poe_set_time
            return True
        return False

    def get_system_power_bank(self):
        try:
            return self.poe_plat.get_current_power_bank()
        except Exception as e:
            self.log.err("Failed to get system power bank: %s" % str(e))
            return None

    def have_psu_event(self):
        cur_power_bank = self.get_system_power_bank()
        if self.last_power_bank != cur_power_bank:
            self.last_power_bank = cur_power_bank
            return True
        return False

    def is_state_changes(self):
        return self.have_set_event() or self.have_psu_event()

    def get_system_running_state(self):
        try:
            return self.poe_plat.get_system_information(False)
        except Exception as e:
            self.log.err("Failed to get system running state: %s" % str(e))
            raise e

    def get_ports_running_state(self):
        try:
            portList = list(range(self.poe_plat.total_poe_port()))
            return self.poe_plat.get_ports_information(portList, False)
        except Exception as e:
            self.log.err("Failed to get ports running state: %s" % str(e))
            raise e

    @PoeAccessExclusiveLock
    def apply_platform_defaults(self):
        try:
            self.poe_plat.init_poe()
            self.update_set_time()
            return True
        except Exception as e:
            self.log.err("An exception when initializing poe chip: %s" % str(e))
            return False

    def collect_general_info(self):
        gen_info = OrderedDict()
        gen_info[PLATFORM] = self.plat_name
        gen_info[POE_AGT_VER] = POE_AGENT_VERSION
        gen_info[POE_CFG_VER] = POE_CONFIG_VERSION
        gen_info[CFG_SERIAL_NUM] = self.cfg_serial_num + 1
        return gen_info

    def get_current_time(self):
        return datetime.now().strftime(TIME_FMT)

    def update_set_time(self):
        self.last_poe_set_time = self.get_current_time()

    def collect_timestamp(self):
        time_stamp = OrderedDict()
        time_stamp[LAST_SAVE_TIME] = self.get_current_time()
        time_stamp[LAST_SET_TIME] = self.last_poe_set_time
        return time_stamp

    @PoeAccessExclusiveLock
    def collect_running_state(self):
        try:
            if self.is_state_changes() == True:
                self.all_port_state = self.get_ports_running_state()
            self.system_state = self.get_system_running_state()

            cur_state = OrderedDict()
            cur_state[GEN_INFO] = self.collect_general_info()
            cur_state[TIMESTAMP] = self.collect_timestamp()
            cur_state[SYS_INFO] = self.system_state
            cur_state[PORT_CONFIGS] = self.all_port_state
            return cur_state
        except Exception as e:
            self.log.err("Failed to collect running state!")
            return None

    def save_poe_cfg(self, poe_cfg, cfg_data):
        try:
            if poe_cfg.is_valid_data(cfg_data) == False:
                self.log.warn("Get invalid cfg data to save!")
                return False

            if poe_cfg.save(cfg_data) == True:
                self.last_cfg_save_time = cfg_data[TIMESTAMP][LAST_SAVE_TIME]
                self.cfg_serial_num = cfg_data[GEN_INFO][CFG_SERIAL_NUM]
                return True
        except Exception as e:
            self.log.err("An exception to save poe cfg: %s" % str(e))
        return False


    def save_curerent_runtime(self):
        if self.runtime_cfg.is_valid():
            copyfile(self.runtime_cfg.path(),
                     self.permanent_cfg.path())

    def autosave_main(self):
        global thread_flag
        self.rt_counter = 0
        self.fail_counter = 0
        while thread_flag is True:
            try:
                if self.rt_counter >= self.cfg_update_intvl_rt:
                    cfg_data = self.collect_running_state()
                    if self.save_poe_cfg(self.runtime_cfg, cfg_data) == True:
                        self.rt_counter = 0
                    else:
                        self.log.warn(
                            "Failed to save cfg data in autosave routine!")
                self.rt_counter += self.autosave_intvl
                time.sleep(self.autosave_intvl)
            except Exception as e:
                self.fail_counter += 1
                self.log.err("An exception in autosave routine: %s, cnt: %d" %
                             (str(e), self.fail_counter))
                time.sleep(1)

    @PoeAccessExclusiveLock
    def apply_cfg_settings(self, poe_cfg):
        data = poe_cfg.load()
        all_port_configs = data[PORT_CONFIGS]
        last_save_time = data[TIMESTAMP][LAST_SAVE_TIME]
        for params in all_port_configs:
            port_id = params.get(PORT_ID) - 1
            poe_port = self.poe_plat.get_poe_port(port_id)
            poe_port.set_all_params(params)
        self.all_port_state = all_port_configs
        self.last_poe_set_time = self.get_current_time()
        self.last_cfg_save_time = last_save_time

    def load_poe_cfg(self, poe_cfg):
        retry = 0
        while retry < self.cfg_load_retry:
            try:
                if poe_cfg.is_valid() == False:
                    self.log.warn("Invalid cfg data to load!")
                    return False
                self.apply_cfg_settings(poe_cfg)
                return True
            except Exception as e:
                self.log.err("An exception to load cfg (%s): %s, retry = %s" %
                             (poe_cfg.path(), str(e), str(retry)))
            retry += 1
            time.sleep(1)
        return False

    def set_poe_agent_state(self, val):
        if val != PoeAgentState.UNCLEAN_START and \
           val != PoeAgentState.CLEAN_START:
           self.log.warn("Invalid poe agent state: %d, skipped!" % val)
        else:
            self.poe_agent_state = val

    def get_poe_agent_stae(self):
        return self.poe_agent_state

    def create_poe_set_ipc(self):
        try:
            os.mkfifo(POE_IPC_EVT)
        except OSError as oe:
            if oe.errno != errno.EEXIST:
                self.log.err("Failed to open named pipe: %s" % str(e))

def get_prev_pid():
    return int(open(POED_PID_PATH, 'r').read())

def is_still_alive(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    else:
        return True

def save_cur_pid():
    open(POED_PID_PATH, 'w').write(str(os.getpid()))

def main(argv):
    global thread_flag
    if os.geteuid() != 0:
        raise RuntimeError("Warning, poed service must be run as root!")

    is_warm_boot = True
    try:
        prevPid = get_prev_pid()
        if is_still_alive(prevPid) == True:
            PoeLog().warn("Previos poed service is still alive!")
            os._exit(-1)
    except:
        is_warm_boot = False
    finally:
        save_cur_pid()

    pa = PoeAgent()
    if pa.plat_supported:
        touch_file(POED_BUSY_FLAG)
        if pa.apply_platform_defaults() == True:
            pa.log.info("Success to apply platform PoE settings!")
        else:
            pa.log.info("Failed to apply platform PoE settings!")

        poe_cfg = pa.permanent_cfg
        if is_warm_boot and pa.runtime_cfg.is_valid():
            poe_cfg = pa.runtime_cfg
        pa.log.info("Configure PoE ports from \"%s\"" % poe_cfg.path())
        touch_file(POED_BUSY_FLAG)
        if pa.load_poe_cfg(poe_cfg) == True:
            pa.log.info("Success to restore port configurations from \"%s\"." % poe_cfg.path())
        else:
            pa.log.warn("Failed to restore port configurations from \"%s\"." % poe_cfg.path())
            if Path(pa.permanent_cfg.path()).exists() == False:
                pa.log.info(
                    "Presistant config file loss, reconstruct \"%s\" config from poe chip runtime setting." % poe_cfg.path())
                cfg_data = pa.collect_running_state()
                if pa.save_poe_cfg(pa.runtime_cfg, cfg_data) == True:
                    copyfile(pa.runtime_cfg.path(),
                             pa.permanent_cfg.path())
            else:
                pa.set_poe_agent_state(PoeAgentState.UNCLEAN_START)

        pa.autosave_thread.start()


        remove_file(POED_BUSY_FLAG)
        pa.create_poe_set_ipc()
        while thread_flag is True:
            try:
                with open(POE_IPC_EVT, 'r') as f:
                    data_list = str(f.read()).split(",")
                    for data in data_list:
                        if data == POECLI_SET:
                            pa.update_set_time()
                            pa.log.info("Receive a set event from poecli!")
                            if pa.rt_counter <pa.cfg_update_intvl_rt:
                                pa.log.info("Reset rt_counter timing: {0}".format(
                                    str(pa.cfg_update_intvl_rt)))
                                pa.rt_counter = pa.cfg_update_intvl_rt
                            break
                        elif data == POECLI_CFG:
                            pa.log.info("Receive a cfg event from poecli!")
                            action=""
                            apply=""
                            file = None
                            if len(data_list) > 1:
                                action = data_list[1]
                                pa.log.info("CFG Action: {0}".format(action))
                                if len(data_list) > 2:
                                    file = data_list[2]
                                    pa.log.info("CFG File: {0}".format(file))
                                    if len(data_list) > 3:
                                        apply = data_list[3]
                                        pa.log.info("CFG Apply: {0}".format(apply))
                                if action==POED_SAVE_ACTION:
                                    if file == None:
                                        pa.log.info(
                                            "CFG Save: Save runtime setting to persistent file")
                                        pa.save_curerent_runtime()
                                    else:
                                        copyfile(pa.runtime_cfg.path(),
                                                 file)
                                        pa.log.info(
                                            "CFG Save: Save runtime setting to {0}".format(file))
                                elif action==POED_LOAD_ACTION:
                                    if file == None:
                                        pa.log.info(
                                            "CFG Load: Load persistent file")
                                        result = pa.load_poe_cfg(pa.permanent_cfg)
                                    else:
                                        pa.log.info(
                                            "CFG Load: Load cfg file from {0}".format(file))
                                        temp_cfg = PoeConfig(file, pa.plat_name)
                                        result = pa.load_poe_cfg(temp_cfg)
                                    if result == True:
                                        pa.update_set_time()


                                break
                        else:
                            pa.log.notice("Receive data: %s, skipped!" % data)


            except Exception as e:
                pa.log.err("An exception to listen poe set event: %s, skipped."
                           % str(e))
    else:
        while thread_flag is True:
            time.sleep(1)

def poed_exit(sig=0, frame=None):
    global thread_flag
    remove_file(POED_BUSY_FLAG)
    thread_flag = False
    exit(0)

if __name__ == "__main__":
    try:
        signal.signal(signal.SIGTERM, poed_exit)
        main(sys.argv)
    finally:
        poed_exit()
