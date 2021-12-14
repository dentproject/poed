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
    return PoePlatform_accton_as4224_52p()

class PoePlatform_accton_as4224_52p(PoeDrv.PoeDriver_microsemi_pd69200):
    def __init__(self):
        PoeDrv.PoeDriver_microsemi_pd69200.__init__(self)
        self.log = PoeLog()
        self._total_poe_port = 48
        self._i2c_bus = 1
        self._i2c_addr = 0x3C
        self._poe_bus = SMBus(self._i2c_bus)

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
        # Clean buffers to reduce retry time
        self.plat_poe_read()

        # Fast compare active and temp matrix
        if fast_temp_matrix_compare(self._default_matrix, self) == False:
            prog_global_matrix = True
        else:
            prog_global_matrix = False

        # Set Temporary Matrix and
        for temp_matrix_mapping in self._default_matrix:
            logic_port = temp_matrix_mapping[0]
            phy_porta = temp_matrix_mapping[1]
            if (config_in == None or config_in == False):
                # Disable all ports
                self.set_port_enDis(logic_port, 0)
                # Set default ports power limit
                self.set_port_power_limit(
                    logic_port, self._port_power_limit)

            if prog_global_matrix == True:
                self.set_temp_matrix(logic_port, phy_porta)

        # Set Power Bank
        self.set_power_bank(1, 800)

        # Set POE Power Management Method
        self.set_pm_method(POE_PD69200_MSG_DATA_PM1_DYNAMIC,
                        POE_PD69200_MSG_DATA_PM2_PPL,
                        POE_PD69200_MSG_DATA_PM3_NO_COND)

        if prog_global_matrix == True:
            print_stderr("Program active matrix, all ports will shutdown a while")
            self.program_active_matrix()
            print_stderr("Program active matrix completed, save platform settings")
            self.save_system_settings()

    def bank_to_psu_str(self, bank):
        powerSrc = "None"
        if bank == 1:
            powerSrc = "PSU1, PSU2"
        return powerSrc
