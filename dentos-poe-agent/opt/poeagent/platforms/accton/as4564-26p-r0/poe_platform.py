
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
    return PoePlatform_accton_as4564_26p()

class PoePlatform_accton_as4564_26p(PoeDrv.PoeDriver_microsemi_pd69200):
    def __init__(self):
        PoeDrv.PoeDriver_microsemi_pd69200.__init__(self)
        self.log = PoeLog()
        self._total_poe_port = 24
        self._i2c_bus = 1
        self._i2c_addr = 0x3C
        self._poe_bus = SMBus(self._i2c_bus)
        # Add read 15byte first to cleanup buffer
        self.plat_poe_read()
        self._4wire_bt = self.support_4wire_bt(3)
        # item in matrix: (logic port, phy port a,  phy port b)
        self._default_matrix = [
            (0, 4, 0xff), (1, 5, 0xff), (2, 6, 0xff), (3, 7, 0xff),
            (4, 1, 0xff), (5, 2, 0xff), (6, 3, 0xff), (7, 0, 0xff),
            (8, 12, 0xff), (9, 13, 0xff), (10, 14, 0xff), (11, 15, 0xff),
            (12, 11, 0xff), (13, 10, 0xff), (14, 9, 0xff), (15, 8, 0xff),
            (16, 22, 21), (17, 20, 23), (18, 19, 18), (19, 17, 16),
            (20, 30, 29), (21, 28, 31), (22, 27, 26), (23, 25, 24),
            (24, 0xff, 0xff), (25, 0xff, 0xff), (26, 0xff, 0xff), (27, 0xff, 0xff),
            (28, 0xff, 0xff), (29, 0xff, 0xff), (30, 0xff, 0xff), (31, 0xff, 0xff),
            (32, 0xff, 0xff), (33, 0xff, 0xff), (34, 0xff, 0xff), (35, 0xff, 0xff),
            (36, 0xff, 0xff), (37, 0xff, 0xff), (38, 0xff, 0xff), (39, 0xff, 0xff),
            (40, 0xff, 0xff), (41, 0xff, 0xff), (42, 0xff, 0xff), (43, 0xff, 0xff),
            (44, 0xff, 0xff), (45, 0xff, 0xff), (46, 0xff, 0xff), (47, 0xff, 0xff)]

        self._max_shutdown_vol = 0x0249 # 58.5 V
        self._min_shutdown_vol = 0x01E0 # 48.0 V
        self._guard_band = 0x0A
        self._default_power_banks = [(1, 520)]

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


        # Create default parameter (Disable, low priority)
        default_param = dict({
            ENDIS: "disable",
            PRIORITY: "low",
        })

        # Set Temporary Matrix and
        for temp_matrix_mapping in self._default_matrix:
            logic_port = temp_matrix_mapping[0]
            phy_porta = temp_matrix_mapping[1]
            phy_portb = temp_matrix_mapping[2]
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
                result = self.set_temp_matrix(logic_port, phy_porta, phy_portb)
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
                "setting": _power_bank,
                CMD_RESULT_RET: result
            })

        # Set opration mode
        for port_id in range(self.total_poe_port()):
            if port_id <= 15:
                result = self.set_bt_port_operation_mode(port_id, 0x9)
            else:
                result = self.set_bt_port_operation_mode(port_id, 0x1)
            ret_item["set_op_mode"].append({
                "idx": port_id,
                CMD_RESULT_RET: result
            })


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
        return ret_item

    def bank_to_psu_str(self, bank):
        powerSrc = "None"
        if bank == 1:
            powerSrc = "PSU1, PSU2"
        return powerSrc
