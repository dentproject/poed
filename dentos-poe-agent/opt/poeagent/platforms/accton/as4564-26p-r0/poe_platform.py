from poe_driver_pd69200_bt_def import *
from poe_common import *
from smbus2 import SMBus, i2c_msg

import os
import sys
import time
import fcntl
import poe_driver_pd69200_bt as PoeDrv

def get_poe_platform():
    return PoePlatform_accton_as4564_26p()

class PoePlatform_accton_as4564_26p(PoeDrv.PoeDriver_microsemi_pd69200):
    def __init__(self):
        PoeDrv.PoeDriver_microsemi_pd69200.__init__(self)
        self.log = PoeLog()
        self._total_poe_port = 24
        self._i2c_bus = 1
        self._i2c_addr = 0x3C
        self._poe_bus = SMBus(self._i2c_bus)
        # Time between commands (from hw spec): 30ms
        self._msg_delay = 0.03
        # Wait time after saving system setting: 50ms
        self._save_sys_delay = 0.05
        # Wait time after restore factory default setting: 100ms
        self._restore_factory_default_delay = 0.1
        # item in matrix: (logic port, phy port)
        self._default_matrix = [
            (0, 4), (1, 5), (2, 6), (3, 7),
            (4, 1), (5, 2), (6, 3), (7, 0),
            (8, 12), (9, 13), (10, 14), (11, 15),
            (12, 11), (13, 10), (14, 9), (15, 8),
            (16, 22), (17, 20), (18, 19), (19, 17),
            (20, 30), (21, 28), (22, 27), (23, 25),
            (24, 0xff), (25, 0xff), (26, 0xff), (27, 0xff),
            (28, 0xff), (29, 0xff), (30, 0xff), (31, 0xff),
            (32, 0xff), (33, 0xff), (34, 0xff), (35, 0xff),
            (36, 0xff), (37, 0xff), (38, 0xff), (39, 0xff),
            (40, 0xff), (41, 0xff), (42, 0xff), (43, 0xff),
            (44, 0xff), (45, 0xff), (46, 0xff), (47, 0xff)]

        self._max_shutdown_vol = 0x0249 # 58.5 V
        self._min_shutdown_vol = 0x01E0 # 48.0 V
        self._guard_band = 0x0A

        #add read 15byte first as init chip
        self._i2c_read(self._bus())

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
        self.set_power_bank(1, 520)

        # Enable all ports
        for port_id in range(self.total_poe_port()):
            self.set_port_enDis(port_id, 1)

        #set opration mode
        for port_id in range(self.total_poe_port()):
            if port_id <= 15:
                self.set_port_op_mode(port_id, 0x9)
            else:
                self.set_port_op_mode(port_id, 0x1)

        # Save POE System Settings
        self.save_system_settings()

    def bank_to_psu_str(self, bank):
        powerSrc = "None"
        if bank == 1:
            powerSrc = "PSU1, PSU2"
        return powerSrc
