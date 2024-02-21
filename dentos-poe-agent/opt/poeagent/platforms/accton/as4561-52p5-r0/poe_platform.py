
from collections import OrderedDict
from poe_driver_pd69200_def import *
from poe_common import *
from poe_common import print_stderr
from smbus2 import SMBus, i2c_msg

import os
import sys
import time
import fcntl
import poe_multi_chip_driver_pd69200 as PoeDrv

def get_poe_platform():
    return PoePlatform_accton_as4561_52p5()

def fast_temp_matrix_compare_multi_chip(def_matrix,plat_obj, chip_index=0):
    get_phya = None
    get_phyb = None
    def_mat_pair =[]
    if len(def_matrix[0]) == 3:
        print_stderr("Select 4-Pair mode")
        four_pair = True
    else:
        print_stderr("Select 2-Pair mode")
        four_pair = False
    for def_mat_pair in def_matrix:
        idx = def_mat_pair[0]
        if chip_index == 0:
            get_phya = plat_obj.get_active_matrix(idx)[ACTIVE_MATRIX_PHYA]
        else:
            get_phya = plat_obj.get_active_matrix(idx, chip_index)[ACTIVE_MATRIX_PHYA]
        if get_phya != def_mat_pair[1]:
            print_stderr("Port map mismatch, run program global matrix")
            return False
        if four_pair == True:
            if chip_index == 0:
                get_phyb = plat_obj.get_active_matrix(idx)[ACTIVE_MATRIX_PHYB]
            else:
                get_phyb = plat_obj.get_active_matrix(idx, chip_index)[ACTIVE_MATRIX_PHYB]
            if get_phyb != def_mat_pair[2]:
                print_stderr("Port map mismatch, run program global matrix")
                return False
    print_stderr("Port map match, skip program global matrix")
    return True

class PoePlatform_accton_as4561_52p5(PoeDrv.PoeDriver_microsemi_multi_chip_pd69200):
    def __init__(self):
        PoeDrv.PoeDriver_microsemi_multi_chip_pd69200.__init__(self)
        self.log = PoeLog()
        self.chip_num = 2
        self._total_poe_port = 48
        self._ports_per_chip = 24
        self._i2c_bus = [17,18]
        self._i2c_addr = [0x3C, 0x38]
        self._poe_bus = [SMBus(17), SMBus(18)]
        # Add read 15byte first to cleanup buffer
        self.plat_poe_read(0)
        self.plat_poe_read(1)
        self._4wire_bt = self.support_4wire_bt(3, 0)
        self._4wire_bt = self.support_4wire_bt(3, 1)
        # item in matrix: (logic port, phy port a,  phy port b) for two chip
        self._default_matrix = [
            (0, 2, 3), (1, 0, 1), (2, 6, 7), (3, 4, 5),
            (4, 10, 11), (5, 8, 9), (6, 14, 15), (7, 12, 13),
            (8, 18, 19), (9, 16, 17), (10, 22, 23), (11, 20, 21),
            (12, 26, 27), (13, 24, 25), (14, 30, 31), (15, 28, 29),
            (16, 34, 35), (17, 32, 33), (18, 38, 39), (19, 36, 37),
            (20, 42, 43), (21, 40, 41), (22, 46, 47), (23, 44, 45),
            (24, 0xff, 0xff), (25, 0xff, 0xff), (26, 0xff, 0xff), (27, 0xff, 0xff),
            (28, 0xff, 0xff), (29, 0xff, 0xff), (30, 0xff, 0xff), (31, 0xff, 0xff),
            (32, 0xff, 0xff), (33, 0xff, 0xff), (34, 0xff, 0xff), (35, 0xff, 0xff),
            (36, 0xff, 0xff), (37, 0xff, 0xff), (38, 0xff, 0xff), (39, 0xff, 0xff),
            (40, 0xff, 0xff), (41, 0xff, 0xff), (42, 0xff, 0xff), (43, 0xff, 0xff),
            (44, 0xff, 0xff), (45, 0xff, 0xff), (46, 0xff, 0xff), (47, 0xff, 0xff)]

        # item in matrix: (logic port, chip)
        self._port_chip_matrix = [
            0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
            0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 1, 1, 1, 1,
            1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1]

        # port 0-23 map to chip 0 port 0-23
        # port 24-47 map to chip 1 port 24-47
        self._logic_port_matrix = [
            0, 1, 2, 3, 4, 5, 6, 7,
            8, 9,10, 11, 12, 13, 14, 15,
            16, 17, 18, 19, 20, 21, 22, 23,
            0, 1, 2, 3, 4, 5, 6, 7,
            8, 9,10, 11, 12, 13, 14, 15,
            16, 17, 18, 19, 20, 21, 22, 23,]

        self._max_shutdown_vol = 0x0249 # 58.5 V
        self._min_shutdown_vol = 0x01E0 # 48.0 V
        self._guard_band = 0x0A
        self._default_power_banks = [(0,0), (1, 1500)]
        #self._port_power_limit = 0x15F90 # 90000 mW

    def total_poe_port(self):
        return self._total_poe_port

    def poe_chip_num(self):
        return self.chip_num

    def get_port_chip(self, port_id):
        return self._port_chip_matrix[port_id]

    def get_logic_port(self, port_id):
        return self._logic_port_matrix[port_id]

    def _bus(self, chip_index=0):
        if self._poe_bus[chip_index].fd is None:
            self._poe_bus[chip_index] = SMBus(self._poe_bus[chip_index])
        return self._poe_bus[chip_index]

    def _i2c_write(self, bus, chip_index, msg, delay = 0.03):
        write = i2c_msg.write(self._i2c_addr[chip_index], msg)
        bus.i2c_rdwr(write)
        time.sleep(delay)

    def _i2c_read(self, bus, chip_index, size = 15):
        read = i2c_msg.read(self._i2c_addr[chip_index], size)
        bus.i2c_rdwr(read)
        msg = list(read)
        return msg

    def plat_poe_write(self, msg, delay, chip_index=0):
        return self._i2c_write(self._bus(chip_index), chip_index, msg, delay)

    def plat_poe_read(self, chip_index=0):
        return self._i2c_read(self._bus(chip_index), chip_index)

    def bus_lock(self, chip_index=0):
        fcntl.flock(self._bus(chip_index).fd, fcntl.LOCK_EX)

    def bus_unlock(self, chip_index=0):
        fcntl.flock(self._bus(chip_index).fd, fcntl.LOCK_UN)

    def init_poe(self, config_in=None):
        version = ["",""]
        max_sd_vol = [0, 0]
        min_sd_vol = [0, 0]
        max_sd_type = 0
        min_sd_type = 1
        poe_chip_0 = 0
        poe_chip_1 = 1
        ret_item = OrderedDict()
        # Clean buffers to reduce retry time
        for chip_index in range(0 , self.chip_num):
            # Fast compare active and temp matrix
            if fast_temp_matrix_compare_multi_chip(self._default_matrix, self, chip_index) == False:
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
                #POWER_LIMIT: self._port_power_limit
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
                    result = self.set_temp_matrix(logic_port, phy_porta, phy_portb, chip_index)
                    set_port_item["set_temp_matrix"].append({
                        "idx": logic_port,
                        CMD_RESULT_RET: result
                    })
            ret_item["set_port_item"] = set_port_item
            # Set Power Bank
            for _power_bank in self._default_power_banks:
                (bank, power_limit) = _power_bank
                result = self.set_power_bank(bank, power_limit, chip_index)
                ret_item["set_power_bank"].append({
                    "setting": _power_bank,
                    CMD_RESULT_RET: result
                })

            # Set opration mode
            for port_id in range(self._ports_per_chip):
                #To set all port to 4P BT 90 W
                result = self.set_bt_port_operation_mode(port_id, 0x0, chip_index)
                ret_item["set_op_mode"].append({
                    "idx": port_id,
                    CMD_RESULT_RET: result
                })

            if prog_global_matrix == True:
                print_stderr(
                    "Program active matrix, all ports will shutdown a while")
                result_prog_matrix = self.program_active_matrix(chip_index)
                print_stderr(
                    "Program active matrix completed, save platform settings to chip")
                result_save_sys = self.save_system_settings(chip_index)
                ret_item["program_active_matrix"] = {
                    CMD_RESULT_RET: result_prog_matrix
                }
                ret_item["save_system_settings"] = {
                    CMD_RESULT_RET: result_save_sys
                }
            version[chip_index] = self.get_poe_versions(chip_index)
            max_sd_vol[chip_index] = self.get_shutdown_voltage(chip_index, max_sd_type) / 10
            min_sd_vol[chip_index] = self.get_shutdown_voltage(chip_index, min_sd_type) / 10

        if version[poe_chip_0] != version[poe_chip_1]:
            print_stderr("PoE chip version is diffetent. The version of PoE chip 0 is {0} and version of PoE chip 1 is {1}" \
                            .format(version[poe_chip_0],  version[poe_chip_1]))
            print_stderr("The max shutdown voltage of PoE chip 0 is {0} V and max shutdown voltage of PoE chip 1 is {1} V" \
                            .format(max_sd_vol[poe_chip_0],  max_sd_vol[poe_chip_1]))
            print_stderr("The min shutdown voltage of PoE chip 0 is {0} V and min shutdown voltage of PoE chip 1 is {1} V" \
                            .format(min_sd_vol[poe_chip_0],  min_sd_vol[poe_chip_1]))

        return ret_item

    def bank_to_psu_str(self, bank, chip_index):
        powerSrc = ""
        if chip_index == 0:
            if bank == 1:
                powerSrc = "PSU1 "
        if chip_index == 1:
            if bank == 1:
                powerSrc = "PSU2"

        return powerSrc
