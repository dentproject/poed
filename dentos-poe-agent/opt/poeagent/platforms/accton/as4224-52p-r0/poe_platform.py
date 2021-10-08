from poe_driver_pd69200_def import *
from poe_common import *
from smbus2 import SMBus, i2c_msg

import os
import sys
import time
import fcntl
import poe_driver_pd69200 as PoeDrv

def get_poe_platform():
    return PoePlatform_accton_as4224_52p()

class PoePlatform_accton_as4224_52p(PoeDrv.PoeDriver_microsemi_pd69200):
    def __init__(self):
        PoeDrv.PoeDriver_microsemi_pd69200.__init__(self)
        self.log = PoeLog()
        self._total_poe_port = 48
        self._4wire_bt = 0
        self._i2c_bus = 1
        self._i2c_addr = 0x3C
        self._poe_bus = SMBus(self._i2c_bus)
        # Time between commands (from hw spec): 30ms
        self._msg_delay = 0.03
        # Wait time after saving system setting: 50ms
        self._save_sys_delay = 0.05
        # Wait time after restore factory default setting(from hw spec): 100ms
        self._restore_factory_default_delay = 0.1

        # item in matrix: (logic port, phy port)
        self._default_matrix = [
            # locgic port
            ( 0,  7), ( 1,  4), ( 2,  5), ( 3,  6), ( 4,  0), ( 5,  1), ( 6,  2), ( 7,  3),
            ( 8, 12), ( 9, 13), (10,  14), (11,  15), (12, 9), (13, 10), (14, 11), (15, 8),
            (16, 20), (17, 21), (18, 22), (19, 23), (20, 17), (21, 18), (22, 19), (23, 16),
            (24, 28), (25, 29), (26, 30), (27, 31), (28, 27), (29, 26), (30, 25), (31, 24),
            (32, 39), (33, 36), (34, 37), (35, 38), (36, 32), (37, 33), (38, 34), (39, 35),
            (40, 47), (41, 44), (42, 45), (43, 46), (44, 40), (45, 41), (46, 42), (47, 43)]

        self._max_shutdown_vol = 0x0249 # 58.5 V
        self._min_shutdown_vol = 0x0190 # 40.0 V
        self._guard_band = 0x01
        self._port_power_limit = 0x7530 # 30000 mW

    def support_4wire_bt(self):
        poe_ver = self.get_poe_versions()
        major_ver = poe_ver[3:4]
        if major_ver == "3":
            self._4wire_bt = 1
        return self._4wire_bt

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
        self.set_power_bank(1, 800)

        # Set Port Power Limit
        for (logic_port, phy_port) in self._default_matrix:
            self.set_port_power_limit(logic_port, self._port_power_limit)

        # Set POE Power Management Method
        self.set_pm_method(POE_PD69200_MSG_DATA_PM1_DYNAMIC,
                           POE_PD69200_MSG_DATA_PM2_PPL,
                           POE_PD69200_MSG_DATA_PM3_NO_COND)

        # Enable all ports
        for port_id in range(self.total_poe_port()):
            self.set_port_enDis(port_id, 1)

        # Save POE System Settings
        self.save_system_settings()

    def bank_to_psu_str(self, bank):
        powerSrc = "None"
        if bank == 1:
            powerSrc = "PSU1, PSU2"
        return powerSrc
