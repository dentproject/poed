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

from agent_constants import AgentConstants
from drivers.i2c_driver import I2cDriver
from drivers.pd69200.poe_driver import PoeDriver_microsemi_pd69200
from drivers.pd69200.poe_driver_def import (
    POE_PD69200_MSG_DATA_PM1_USER_DEFINED,
    POE_PD69200_MSG_DATA_PM2_PPL,
    POE_PD69200_MSG_DATA_PM3_NO_COND,
    POE_PD69200_MSG_DATA_PROTOCOL_ATAF,
    POE_PD69200_MSG_DATA_SUM_AS_TPPL_STATIC,
    POE_PD69200_MSG_SUB2_ALL_CHANNEL,
)
from poe_common import *
from poe_log import PoeLog
from poe_platform import PoePlatform


class Tn48mPoe(PoePlatform):
    # Delta TN48M-P
    def __init__(self):
        self._max_shutdown_vol = 0x0239  # 56.9 V
        self._min_shutdown_vol = 0x01F5  # 50.1 V
        self._guard_band = 0x01
        """
        +-----------------------------------------------+
        | Power Banks | PSU1 PG | PSU2 PG | Power Limit |
        |-----------------------------------------------|
        |   Bank 13   |    NO   |   YES   |    680 W    |
        |-----------------------------------------------|
        |   Bank 14   |   YES   |    NO   |    680 W    |
        |-----------------------------------------------|
        |   Bank 15   |   YES   |   YES   |   1500 W    |
        +-----------------------------------------------+
        """
        self._default_power_banks = [(13, 680), (14, 680), (15, 1500)]
        self._default_port_power_limit = 0x7530  # 30000 mW
        self._bus_driver = I2cDriver(i2c_bus=0x01, i2c_addr=0x3C)
        self._port_count = 48
        PoeDriver_microsemi_pd69200.__init__(
            self,
            self._bus_driver,
            self.port_count(),
            self._max_shutdown_vol,
            self._min_shutdown_vol,
            self._guard_band,
            self.power_bank_to_str,
        )
        self._log = PoeLog()

        # Clear the I2C buffer.
        self._bus_driver.read_message()

        # Mapping: (logical port, phy port)
        self._port_matrix = [
            (0, 2),
            (1, 3),
            (2, 0),
            (3, 1),
            (4, 5),
            (5, 4),
            (6, 7),
            (7, 6),
            (8, 10),
            (9, 11),
            (10, 8),
            (11, 9),
            (12, 13),
            (13, 12),
            (14, 15),
            (15, 14),
            (16, 21),
            (17, 20),
            (18, 23),
            (19, 22),
            (20, 18),
            (21, 19),
            (22, 16),
            (23, 17),
            (24, 29),
            (25, 28),
            (26, 31),
            (27, 30),
            (28, 26),
            (29, 27),
            (30, 24),
            (31, 25),
            (32, 37),
            (33, 36),
            (34, 39),
            (35, 38),
            (36, 34),
            (37, 35),
            (38, 32),
            (39, 33),
            (40, 45),
            (41, 44),
            (42, 47),
            (43, 46),
            (44, 42),
            (45, 43),
            (46, 40),
            (47, 41),
        ]

        # Ignore default power limit allocation.
        self._default_power_limits = {}


    def port_count(self) -> int:
        """Get the total PoE port count

        Returns:
            int: Port count
        """
        return self._port_count

    @property
    def default_power_limits(self) -> dict[tuple[int, int], int]:
        """Get the port ranges default power limits

        Returns:
            dict[tuple[int, int], int]: Default limits as a dictionary
        """
        return self._default_power_limits

    def init_poe(self, skip_port_init: bool) -> dict:
        """Initialize the PoE ports, power bank config and
        each port operation mode. If skip_port_init is true,
        will not set the default port parameters

        The global port matrix will be reprogrammed, only if
        the actual matrix is different than the active port matrix.

        Args:
            skip_port_init (bool): Skip port init flag

        Returns:
            dict: Result dictionary. Contains the result for each
            individual operation
        """
        # Clear the I2C buffer.
        self._bus_driver.read_message()

        # Default port params to initialize with.
        port_default_params = {
            ENDIS: AgentConstants.ENABLE,
            PRIORITY: "low",
            POWER_LIMIT: self._default_port_power_limit,
        }

        # Determine if we need to reprogram the port matrix.
        program_port_matrix = False
        if not is_active_port_matrix_different(self._port_matrix, self.get_active_matrix):
            program_port_matrix = True

        # Configure the power bank power and voltage limits with the actual
        # values.
        result = OrderedDict()
        result["power_bank"] = []
        for power_bank in self._default_power_banks:
            (bank_index, power_limit) = power_bank
            result["power_bank"].append(
                {
                    "bank_details": power_bank,
                    AgentConstants.CMD_RESULT_RET: self.set_power_bank(bank_index, power_limit),
                }
            )
            # Confirm that the power bank was successfully configured.
            power_bank_details = self.get_power_bank(bank_index)
            result["power_bank"][-1][AgentConstants.CMD_RESULT_RET] = (
                0 if power_bank_details[POWER_LIMIT] == power_limit else 1
            )
            # Prevent enabling or changing any port parameter if the power bank
            # configuration failed.
            if power_bank_details[POWER_LIMIT] != power_limit:
                return result

        set_port_results = {}
        set_port_results["set_port_params"] = []
        if program_port_matrix:
            set_port_results["set_temp_matrix"] = []
        if skip_port_init:
            self._log.dbg("Skipping port initialization")
        for ports in self._port_matrix:
            port_index, phy_port = ports
            if not skip_port_init:
                port = self.get_poe_port(port_index)
                set_port_results["set_port_params"].append(
                    {"idx": port_index, AgentConstants.CMD_RESULT_RET: port.set_all_params(port_default_params)}
                )
            if program_port_matrix:
                # The temporary port matrix must be set before saving the
                # global matrix.
                self._log.info("Setting the temporary port matrix...")
                set_port_results["set_temp_matrix"].append(
                    {"idx": port_index, AgentConstants.CMD_RESULT_RET: self.set_temp_matrix(port_index, phy_port)}
                )
        result["port_init"] = set_port_results

        # Set power management mode across all ports.
        result["set_power_management"] = {
            AgentConstants.CMD_RESULT_RET: self.set_pm_method(
                POE_PD69200_MSG_DATA_PM1_USER_DEFINED, POE_PD69200_MSG_DATA_PM2_PPL, POE_PD69200_MSG_DATA_PM3_NO_COND
            )
        }

        if program_port_matrix:
            # Persist global port matrix and save system settings.
            self._log.notice("Ports will be shutdown while reprogramming " "the active port matrix")
            result["program_active_matrix"] = {AgentConstants.CMD_RESULT_RET: self.program_active_matrix()}
            self._log.notice("Programming port matrix completed, " "flushing platform settings...")
            result["save_system_settings"] = {AgentConstants.CMD_RESULT_RET: self.save_system_settings()}

        return result

    def power_bank_to_str(self, bank: int) -> str:
        """Stringify the given power bank as a combination
        of one or more PSUs

        Args:
            bank (int): Power bank index

        Returns:
            str: Power bank as a string
        """
        psu = "None"
        if bank == 13:
            psu = "PSU2"
        elif bank == 14:
            psu = "PSU1"
        elif bank == 15:
            psu = "PSU1, PSU2"
        return psu

    def _reset_cpld(self) -> None:
        pass


def get_poe_platform():
    return Tn48mPoe()
