import time
from collections import OrderedDict

from agent_constants import AgentConstants
from i2c_driver import I2cDriver
from pd69200.poe_driver import PoeDriver_microsemi_pd69200
from pd69200.poe_driver_def import (
    POE_PD69200_BT_MSG_DATA_PORT_MODE_TPPL,
    POE_PD69200_BT_MSG_DATA_PORT_OP_MODE_4P_30W_2P_30W,
    POE_PD69200_BT_MSG_DATA_PORT_OP_MODE_4P_60W_2P_30W,
)
from poe_common import *
from poe_log import PoeLog
from poe_platform import PoePlatform
from smbus2 import SMBus, i2c_msg


class As4564_26p(PoePlatform):
    # Accton AS4564-26P
    def __init__(self) -> None:
        self._echo = 0x00
        self._max_shutdown_vol = 0x0249  # 58.5 V
        self._min_shutdown_vol = 0x01E0  # 48.0 V
        self._guard_band = 0x0A
        self._default_power_banks = [(1, 520)]
        self._bus_driver = I2cDriver(i2c_bus=0x01, i2c_addr=0x3C)
        self._port_count = 24
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

        # Mapping: (logical port, phy port a,  phy port b)
        self._port_matrix = [
            (0, 4, 0xFF),
            (1, 5, 0xFF),
            (2, 6, 0xFF),
            (3, 7, 0xFF),
            (4, 1, 0xFF),
            (5, 2, 0xFF),
            (6, 3, 0xFF),
            (7, 0, 0xFF),
            (8, 12, 0xFF),
            (9, 13, 0xFF),
            (10, 14, 0xFF),
            (11, 15, 0xFF),
            (12, 11, 0xFF),
            (13, 10, 0xFF),
            (14, 9, 0xFF),
            (15, 8, 0xFF),
            (16, 22, 21),
            (17, 20, 23),
            (18, 19, 18),
            (19, 17, 16),
            (20, 30, 29),
            (21, 28, 31),
            (22, 27, 26),
            (23, 25, 24),
            (24, 0xFF, 0xFF),
            (25, 0xFF, 0xFF),
            (26, 0xFF, 0xFF),
            (27, 0xFF, 0xFF),
            (28, 0xFF, 0xFF),
            (29, 0xFF, 0xFF),
            (30, 0xFF, 0xFF),
            (31, 0xFF, 0xFF),
            (32, 0xFF, 0xFF),
            (33, 0xFF, 0xFF),
            (34, 0xFF, 0xFF),
            (35, 0xFF, 0xFF),
            (36, 0xFF, 0xFF),
            (37, 0xFF, 0xFF),
            (38, 0xFF, 0xFF),
            (39, 0xFF, 0xFF),
            (40, 0xFF, 0xFF),
            (41, 0xFF, 0xFF),
            (42, 0xFF, 0xFF),
            (43, 0xFF, 0xFF),
            (44, 0xFF, 0xFF),
            (45, 0xFF, 0xFF),
            (46, 0xFF, 0xFF),
            (47, 0xFF, 0xFF),
        ]

        # Minimum firmware major for BT support is 3.x
        self.supports_bt_protocol(3)

        # Map the default port power limit (in W) for the ECAs (class 6)
        # and for cameras (class 3 and 4).
        self._default_power_limits = {3: 14, 4: 14, 6: 45}


    def port_count(self) -> int:
        """Get the total PoE port count

        Returns:
        int: Port count
        """
        return self._port_count


    @property
    def default_power_limits(self) -> dict[int, int]:
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
        port_default_params = {ENDIS: AgentConstants.ENABLE, PRIORITY: "low"}

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
            port_index, phy_port_a, phy_port_b = ports
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
                    {
                        "idx": port_index,
                        AgentConstants.CMD_RESULT_RET: self.set_temp_matrix(port_index, phy_port_a, phy_port_b),
                    }
                )
        result["port_init"] = set_port_results

        # Set port operation mode (i.e., limit first 16 ports to 30W/at, and
        # the rest to 60W/bt).
        # Configure the port power management to use the port TPPL for
        # computing the available power.
        result["port_operation_mode"] = []
        for port_id in range(self.port_count()):
            result["port_operation_mode"].append(
                {
                    "idx": port_id,
                    AgentConstants.CMD_RESULT_RET: self.bt_set_port_params(
                        port_id,
                        POE_PD69200_BT_MSG_DATA_PORT_MODE_TPPL,
                        (
                            POE_PD69200_BT_MSG_DATA_PORT_OP_MODE_4P_60W_2P_30W
                            if port_id > 15
                            else POE_PD69200_BT_MSG_DATA_PORT_OP_MODE_4P_30W_2P_30W
                        ),
                    ),
                }
            )

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
        if bank == 1:
            psu = "PSU1"
        return psu

    def _reset_cpld(self) -> None:
        self._log.info("Resetting the PoE chipset via CPLD")

        bus = SMBus(0x00)
        for msg in (
            i2c_msg.write(0x40, [0xE0, 0x01]),
            i2c_msg.write(0x40, [0x11, 0xFB]),
            i2c_msg.write(0x40, [0x11, 0xFF]),
        ):
            bus.i2c_rdwr(msg)

        time.sleep(self._reset_poe_chip_delay)


def get_poe_platform():
    return As4564_26p()
