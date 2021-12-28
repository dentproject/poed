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
from collections import OrderedDict
from poe_driver_pd69200_def import *
from poe_common import *
from poe_common import print_stderr
from smbus2 import SMBus, i2c_msg

import os
import sys
import time
import fcntl
import poe_driver_pd69200 as PoeDrv

def get_poe_platform():
    return PoePlatform_delta_tn48m_poe()

class PoePlatform_delta_tn48m_poe(PoeDrv.PoeDriver_microsemi_pd69200):
    def __init__(self):
        PoeDrv.PoeDriver_microsemi_pd69200.__init__(self)
        self.log = PoeLog()
        self._total_poe_port = 48
        self._i2c_bus = 1
        self._i2c_addr = 0x3C
        self._poe_bus = SMBus(self._i2c_bus)

        # Add read 15byte first to cleanup buffer
        self.plat_poe_read()

        # Time between commands (from hw spec): 30ms
        self._msg_delay = 0.03
        # Wait time after saving system setting: 50ms
        self._save_sys_delay = 0.05

        # item in matrix: (logic port, phy port)
        self._default_matrix = [
            ( 0,  2), ( 1,  3), ( 2,  0), ( 3,  1), ( 4,  5), ( 5,  4), ( 6,  7), ( 7,  6),
            ( 8, 10), ( 9, 11), (10,  8), (11,  9), (12, 13), (13, 12), (14, 15), (15, 14),
            (16, 21), (17, 20), (18, 23), (19, 22), (20, 18), (21, 19), (22, 16), (23, 17),
            (24, 29), (25, 28), (26, 31), (27, 30), (28, 26), (29, 27), (30, 24), (31, 25),
            (32, 37), (33, 36), (34, 39), (35, 38), (36, 34), (37, 35), (38, 32), (39, 33),
            (40, 45), (41, 44), (42, 47), (43, 46), (44, 42), (45, 43), (46, 40), (47, 41)]

        '''
        +-----------------------------------------------+
        | Power Banks | PSU1 PG | PSU2 PG | Power Limit |
        |-----------------------------------------------|
        |   Bank 13   |    NO   |   YES   |    680 W    |
        |-----------------------------------------------|
        |   Bank 14   |   YES   |    NO   |    680 W    |
        |-----------------------------------------------|
        |   Bank 15   |   YES   |   YES   |   1500 W    |
        +-----------------------------------------------+
        item in power bank: (bank, power limit)
        '''
        self._default_power_banks = [(13, 680), (14, 680), (15, 1500)]
        self._max_shutdown_vol = 0x0239 # 56.9 V
        self._min_shutdown_vol = 0x01F5 # 50.1 V
        self._guard_band = 0x01
        self._port_power_limit = 0x7530 # 30000 mW

    def total_poe_port(self):
        return self._total_poe_port

    def _bus(self):
        if self._poe_bus.fd is None:
            self._poe_bus = SMBus(self._poe_bus)
        return self._poe_bus

    def _i2c_write(self, bus, msg, delay = 0.03):
        write = i2c_msg.write(self._i2c_addr, msg)
        bus.i2c_rdwr(write)
        time.sleep(delay)

    def _i2c_read(self, bus, size = 15):
        read = i2c_msg.read(self._i2c_addr, size)
        bus.i2c_rdwr(read)
        msg = list(read)
        return msg

    def plat_poe_write(self, msg, delay):
        return self._i2c_write(self._bus(), msg, delay)

    def plat_poe_read(self):
        return self._i2c_read(self._bus())

    def bus_lock(self):
        fcntl.flock(self._bus().fd, fcntl.LOCK_EX)

    def bus_unlock(self):
        fcntl.flock(self._bus().fd, fcntl.LOCK_UN)

    def init_poe(self, config_in=None):
        ret_item = OrderedDict()
        # Clean buffers to reduce retry time
        self.plat_poe_read()

        # Fast compare active and temp matrix
        if fast_temp_matrix_compare(self._default_matrix, self) == False:
            prog_global_matrix = True
        else:
            prog_global_matrix = False

        # Port result list
        set_port_item = dict()
        # Default values
        set_port_item["set_port_params"] = []
        set_port_item["set_temp_matrix"] = []
        ret_item["set_power_bank"] = []
        ret_item["set_op_mode"] = []
        result_prog_matrix = None
        result_save_sys = None

        # Create default parameter (Disable, low priority, Apply default power Limit)
        default_param = dict({
            ENDIS: "disable",
            PRIORITY: "low",
            POWER_LIMIT: self._port_power_limit
        })

        # Set Temporary Matrix and port default
        for temp_matrix_mapping in self._default_matrix:
            logic_port = temp_matrix_mapping[0]
            phy_porta = temp_matrix_mapping[1]
            if config_in == None:
                port = self.get_poe_port(logic_port)
                result = port.set_all_params(default_param)
                set_port_item["set_port_params"].append({
                        "idx": logic_port,
                        CMD_RESULT_RET: result
                    })
            elif config_in == True:
                # Preserve current state
                pass

            if prog_global_matrix == True:
                result = self.set_temp_matrix(logic_port, phy_porta)
                set_port_item["set_temp_matrix"].append({
                    "idx": logic_port,
                    CMD_RESULT_RET: result
                })
        ret_item["set_port_item"] = set_port_item

        # Set Power Bank
        for _power_bank in self._default_power_banks:
            (bank, power_limit) = _power_bank
            result = self.set_power_bank(bank, power_limit)
            ret_item["set_power_bank"].append({
                "setting" : _power_bank,
                CMD_RESULT_RET: result
            })

        # Set POE Power Management Method
        result = self.set_pm_method(POE_PD69200_MSG_DATA_PM1_DYNAMIC,
                                    POE_PD69200_MSG_DATA_PM2_PPL,
                                    POE_PD69200_MSG_DATA_PM3_NO_COND)
        ret_item["set_pm_method"] = {
            CMD_RESULT_RET: result
        }

        if prog_global_matrix == True:
            print_stderr(
                "Program active matrix, all ports will shutdown a while")
            result_prog_matrix = self.program_active_matrix()
            print_stderr(
                "Program active matrix completed, save platform settings to chip")
            result_save_sys = self.save_system_settings()
            ret_item["program_active_matrix"] = {
                CMD_RESULT_RET: result_prog_matrix
            }
            ret_item["save_system_settings"] = {
                CMD_RESULT_RET: result_save_sys
            }
        # print_stderr("init_poe result: {0}".format(str(ret_item)))

        return ret_item

    def bank_to_psu_str(self, bank):
        powerSrc = "None"
        if bank == 13:
            powerSrc = "PSU2"
        elif bank == 14:
            powerSrc = "PSU1"
        elif bank == 15:
            powerSrc = "PSU1, PSU2"
        return powerSrc
