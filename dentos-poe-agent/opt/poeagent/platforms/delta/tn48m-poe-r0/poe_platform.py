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
from poe_driver_pd69200_def import *
from poe_common import *
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

    def init_poe(self):
        # Set Temporary Matrix
        for (logic_port, phy_port) in self._default_matrix:
            self.set_temp_matrix(logic_port, phy_port)
        self.program_active_matrix()

        # Disable all ports first
        for port_id in range(self.total_poe_port()):
            self.set_port_enDis(port_id, 0)

        # Set Power Bank
        for (bank, power_limit) in self._default_power_banks:
            self.set_power_bank(bank, power_limit)

        # Set Port Power Limit
        for (logic_port, phy_port) in self._default_matrix:
            self.set_port_power_limit(logic_port, self._port_power_limit)

        # Set POE Power Management Method
        self.set_pm_method(POE_PD69200_MSG_DATA_PM1_DYNAMIC,
                           POE_PD69200_MSG_DATA_PM2_PPL,
                           POE_PD69200_MSG_DATA_PM3_NO_COND)


    def bank_to_psu_str(self, bank):
        powerSrc = "None"
        if bank == 13:
            powerSrc = "PSU2"
        elif bank == 14:
            powerSrc = "PSU1"
        elif bank == 15:
            powerSrc = "PSU1, PSU2"
        return powerSrc
