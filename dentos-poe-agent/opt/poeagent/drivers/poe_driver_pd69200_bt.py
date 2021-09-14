import time
import sys
import os
from collections import OrderedDict
from poe_common import *
from poe_driver_pd69200_bt_def import *

class PoeCommExclusiveLock(object):
    def __call__(self, comm):
        def wrap_comm(*args, **kargs):
            poe_plat = args[0]
            try:
                poe_plat.bus_lock()
                result = comm(*args, **kargs)
            except Exception as e:
                raise e
            finally:
                poe_plat.bus_unlock()
            return result
        return wrap_comm

class PoeDriver_microsemi_pd69200(object):
    def __init__(self):
        self._echo = 0x00

    def _calc_msg_echo(self):
        self._echo += 1
        if (self._echo == 0xff):
            self._echo = 0x00
        return self._echo

    def _calc_msg_csum(self, msg):
        if len(msg) > POE_PD69200_MSG_LEN - POE_PD69200_MSG_CSUM_LEN:
            raise RuntimeError("Invalid POE message Length: %d" % len(msg))

        csum16 = 0
        for data in msg:
            csum16 += data
        csum16 = (csum16 & 0xffff)
        return [csum16 >> 8, csum16 & 0xff]

    def _build_tx_msg(self, command):
        if len(command) > POE_PD69200_MSG_LEN - POE_PD69200_MSG_CSUM_LEN:
            raise RuntimeError(
                "Invalid POE Tx command Length: %d" % len(command))

        tx_msg = command[:]
        lenN = POE_PD69200_MSG_LEN - len(tx_msg) - POE_PD69200_MSG_CSUM_LEN
        for i in range(lenN):
            tx_msg.append(POE_PD69200_MSG_N)
        tx_msg += self._calc_msg_csum(tx_msg)
        return tx_msg

    def _xmit(self, msg, delay):
        if len(msg) != POE_PD69200_MSG_LEN:
            raise RuntimeError("Invalid POE Tx message Length: %d" % len(msg))
        self.plat_poe_write(msg, delay)

    def _recv(self):
        return self.plat_poe_read()

    def _check_rx_msg(self, rx_msg, tx_msg):
        if len(rx_msg) != POE_PD69200_MSG_LEN:
            raise RuntimeError(
                "Received POE message Length is invalid: %d" % len(rx_msg))
        if rx_msg.count(0x00) == POE_PD69200_MSG_LEN:
            raise RuntimeError("POE RX is not ready")

        tx_key, rx_key = tx_msg[POE_PD69200_MSG_OFFSET_KEY], rx_msg[POE_PD69200_MSG_OFFSET_KEY]
        if (tx_key == POE_PD69200_MSG_KEY_COMMAND or tx_key == POE_PD69200_MSG_KEY_PROGRAM) and \
                rx_key != POE_PD69200_MSG_KEY_REPORT:
            raise RuntimeError("Key field in Tx/Rx message is mismatch,\
                               Tx key is %d, Rx key should be %d, but received %d" %
                               (tx_key, POE_PD69200_MSG_KEY_REPORT, rx_key))
        if tx_key == POE_PD69200_MSG_KEY_REQUEST and rx_key != POE_PD69200_MSG_KEY_TELEMETRY:
            raise RuntimeError("Key field in Tx/Rx message is mismatch,\
                               Tx key is %d, Rx key should be %d, but received %d" %
                               (tx_key, POE_PD69200_MSG_KEY_REPORT, rx_key))

        tx_echo, rx_echo = tx_msg[POE_PD69200_MSG_OFFSET_ECHO], rx_msg[POE_PD69200_MSG_OFFSET_ECHO]
        if rx_echo != tx_echo:
            raise RuntimeError("Echo field in Tx/Rx message is mismatch,\
                               Tx Echo is %d, Rx Echo is %d" % (tx_echo, rx_echo))

        csum = self._calc_msg_csum(rx_msg[0:POE_PD69200_MSG_OFFSET_CSUM_H])
        if (rx_msg[POE_PD69200_MSG_OFFSET_CSUM_H] != csum[0] or
                rx_msg[POE_PD69200_MSG_OFFSET_CSUM_L] != csum[1]):
            raise RuntimeError("Invalid checksum in POE Rx message")

    @PoeCommExclusiveLock()
    def _communicate(self, tx_msg, delay):
        retry = 0
        while retry < POE_PD69200_COMM_RETRY_TIMES:
            try:
                self._xmit(tx_msg, delay)
                rx_msg = self._recv()
                self._check_rx_msg(rx_msg, tx_msg)
                return rx_msg
            except Exception as e:
                rx_msg = self._recv()
                retry += 1
        raise RuntimeError(
            "Problems in running poe communication protocol - %s" % str(e))

    def _run_communication_protocol(self, command, delay, msg_type=None):
        tx_msg = self._build_tx_msg(command)
        rx_msg = self._communicate(tx_msg, self._msg_delay)
        if rx_msg is not None and msg_type is not None:
            result = PoeMsgParser().parse(rx_msg, msg_type)
            return result

    def reset_poe(self):
        command = [POE_PD69200_MSG_KEY_COMMAND,
                   self._calc_msg_echo(),
                   POE_PD69200_MSG_SUB_GLOBAL,
                   POE_PD69200_MSG_SUB1_RESET,
                   0x00,
                   POE_PD69200_MSG_SUB1_RESET,
                   0x00,
                   POE_PD69200_MSG_SUB1_RESET]
        self._run_communication_protocol(command, self._msg_delay)

    def restore_factory_default(self):
        command = [POE_PD69200_MSG_KEY_PROGRAM,
                   self._calc_msg_echo(),
                   POE_PD69200_MSG_SUB_RESOTRE_FACT]
        self._run_communication_protocol(command, self._msg_delay)

    def save_system_settings(self):
        command = [POE_PD69200_MSG_KEY_PROGRAM,
                   self._calc_msg_echo(),
                   POE_PD69200_MSG_SUB_E2,
                   POE_PD69200_MSG_SUB1_SAVE_CONFIG]
        self._run_communication_protocol(command, self._save_sys_delay)

    def set_user_byte_to_save(self, user_val):
        command = [POE_PD69200_MSG_KEY_PROGRAM,
                   self._calc_msg_echo(),
                   POE_PD69200_MSG_SUB_USER_BYTE,
                   user_val]
        self._run_communication_protocol(command, self._save_sys_delay)

    # System status function
    def set_system_status(self, priv_label):
        command = [POE_PD69200_MSG_KEY_COMMAND,
                   self._calc_msg_echo(),
                   POE_PD69200_MSG_SUB_GLOBAL,
                   POE_PD69200_MSG_SUB1_SYSTEM_STATUS,
                   priv_label]
        self._run_communication_protocol(command, self._msg_delay)

    def get_system_status(self):
        command = [POE_PD69200_MSG_KEY_REQUEST,
                   self._calc_msg_echo(),
                   POE_PD69200_MSG_SUB_GLOBAL,
                   POE_PD69200_MSG_SUB1_SYSTEM_STATUS]
        return self._run_communication_protocol(command, self._msg_delay,
                                                PoeMsgParser.MSG_SYSTEM_STATUS)

    def set_individual_mask(self, mask_num, enDis):
        command = [POE_PD69200_MSG_KEY_COMMAND,
                   self._calc_msg_echo(),
                   POE_PD69200_MSG_SUB_GLOBAL,
                   POE_PD69200_MSG_SUB1_INDV_MSK,
                   mask_num,
                   enDis]
        return self._run_communication_protocol(command, self._msg_delay)

    def get_individual_mask(self, mask_num):
        command = [POE_PD69200_MSG_KEY_REQUEST,
                   self._calc_msg_echo(),
                   POE_PD69200_MSG_SUB_GLOBAL,
                   POE_PD69200_MSG_SUB1_INDV_MSK,
                   mask_num]
        return self._run_communication_protocol(command, self._msg_delay,
                                                PoeMsgParser.MSG_INDV_MASK)

    def get_software_version(self):
        command = [POE_PD69200_MSG_KEY_REQUEST,
                   self._calc_msg_echo(),
                   POE_PD69200_MSG_SUB_GLOBAL,
                   POE_PD69200_MSG_SUB1_VERSIONZ,
                   POE_PD69200_MSG_SUB2_SW_VERSION]
        return self._run_communication_protocol(command, self._msg_delay,
                                                PoeMsgParser.MSG_SW_VERSION)

    def set_temp_matrix(self, logic_port, phy_port_a, phy_port_b=0xFF):
        command = [POE_PD69200_MSG_KEY_COMMAND,
                   self._calc_msg_echo(),
                   POE_PD69200_MSG_SUB_CHANNEL,
                   POE_PD69200_MSG_SUB1_TEMP_MATRIX,
                   logic_port, phy_port_a, phy_port_b]
        self._run_communication_protocol(command, self._msg_delay)

    def get_temp_matrix(self, logic_port):
        command = [POE_PD69200_MSG_KEY_REQUEST,
                   self._calc_msg_echo(),
                   POE_PD69200_MSG_SUB_CHANNEL,
                   POE_PD69200_MSG_SUB1_TEMP_MATRIX,
                   logic_port]
        return self._run_communication_protocol(command, self._msg_delay)

    def program_active_matrix(self):
        command = [POE_PD69200_MSG_KEY_COMMAND,
                   self._calc_msg_echo(),
                   POE_PD69200_MSG_SUB_GLOBAL,
                   POE_PD69200_MSG_SUB1_TEMP_MATRIX]
        self._run_communication_protocol(command, self._msg_delay)

    def get_active_matrix(self, logic_port):
        command = [POE_PD69200_MSG_KEY_REQUEST,
                   self._calc_msg_echo(),
                   POE_PD69200_MSG_SUB_CHANNEL,
                   POE_PD69200_MSG_SUB1_CH_MATRIX,
                   logic_port]
        return self._run_communication_protocol(command, self._msg_delay)

    def set_port_enDis(self, logic_port, EnDis):
        command = [POE_PD69200_MSG_KEY_COMMAND,
                   self._calc_msg_echo(),
                   POE_PD69200_MSG_SUB_CHANNEL,
                   POE_PD69200_MSG_SUB1_BT_PORTS_PARAMETERS,
                   logic_port,
                   POE_PD69200_MSG_DATA_CMD_ENDIS_ONLY | EnDis,
                   POE_PD69200_MSG_DATA_PORT_MODE_NOT_CHANGE | POE_PD69200_MSG_DATA_PORT_CLASS_ERROR_NOT_CHANGE,
                   POE_PD69200_MSG_DATA_PORT_OP_MODE_NOT_CHANGE,
                   0x0,
                   POE_PD69200_MSG_DATA_PORT_PRIORITY_NO_CHANGE]
        self._run_communication_protocol(command, self._msg_delay)

    def get_all_ports_enDis(self):
        command = [POE_PD69200_MSG_KEY_REQUEST,
                   self._calc_msg_echo(),
                   POE_PD69200_MSG_SUB_GLOBAL,
                   POE_PD69200_MSG_SUB1_EN_DIS]
        return self._run_communication_protocol(command, self._msg_delay,
                                                PoeMsgParser.MSG_ALL_PORTS_ENDIS)

    all_ports_enDis = property(get_all_ports_enDis, None)

    def set_port_priority(self, logic_port, priority):
        command = [POE_PD69200_MSG_KEY_COMMAND,
                   self._calc_msg_echo(),
                   POE_PD69200_MSG_SUB_CHANNEL,
                   POE_PD69200_MSG_SUB1_BT_PORTS_PARAMETERS,
                   logic_port,
                   POE_PD69200_MSG_DATA_CMD_No_CHAGNE,
                   POE_PD69200_MSG_DATA_PORT_MODE_NOT_CHANGE | POE_PD69200_MSG_DATA_PORT_CLASS_ERROR_NOT_CHANGE,
                   POE_PD69200_MSG_DATA_PORT_OP_MODE_NOT_CHANGE,
                   0x0,
                   priority]
        self._run_communication_protocol(command, self._msg_delay)

    def get_port_parameters(self, logic_port):
        command = [POE_PD69200_MSG_KEY_REQUEST,
                   self._calc_msg_echo(),
                   POE_PD69200_MSG_SUB_CHANNEL,
                   POE_PD69200_MSG_SUB1_BT_PORTS_PARAMETERS,
                   logic_port]
        return self._run_communication_protocol(command, self._msg_delay,
                                                PoeMsgParser.MSG_PORT_STATUS_PARAMETERS)

    def get_port_class(self, logic_port):
        command = [POE_PD69200_MSG_KEY_REQUEST,
                   self._calc_msg_echo(),
                   POE_PD69200_MSG_SUB_CHANNEL,
                   POE_PD69200_MSG_SUB1_BT_PORTS_CLASS,
                   logic_port]
        return self._run_communication_protocol(command, self._msg_delay,
                                                PoeMsgParser.MSG_PORT_CLASS)

    def get_total_power(self):
        command = [POE_PD69200_MSG_KEY_REQUEST,
                   self._calc_msg_echo(),
                   POE_PD69200_MSG_SUB_GLOBAL,
                   POE_PD69200_MSG_SUB1_SUPPLY,
                   POE_PD69200_MSG_SUB2_TOTAL_PWR]
        return self._run_communication_protocol(command, self._msg_delay)

    def set_power_bank(self, bank, power_limit):
        command = [POE_PD69200_MSG_KEY_COMMAND,
                   self._calc_msg_echo(),
                   POE_PD69200_MSG_SUB_GLOBAL,
                   POE_PD69200_MSG_SUB1_SUPPLY,
                   POE_PD69200_MSG_SUB2_PWR_BUDGET,
                   bank,
                   power_limit >> 8,
                   power_limit & 0xff,
                   self._max_shutdown_vol >> 8,
                   self._max_shutdown_vol & 0xff,
                   self._min_shutdown_vol >> 8,
                   self._min_shutdown_vol & 0xff,
                   self._guard_band]
        self._run_communication_protocol(command, self._msg_delay)

    def get_power_bank(self, bank):
        command = [POE_PD69200_MSG_KEY_REQUEST,
                   self._calc_msg_echo(),
                   POE_PD69200_MSG_SUB_GLOBAL,
                   POE_PD69200_MSG_SUB1_SUPPLY,
                   POE_PD69200_MSG_SUB2_PWR_BUDGET,
                   bank]
        return self._run_communication_protocol(command, self._msg_delay)

    def get_power_supply_params(self):
        command = [POE_PD69200_MSG_KEY_REQUEST,
                   self._calc_msg_echo(),
                   POE_PD69200_MSG_SUB_GLOBAL,
                   POE_PD69200_MSG_SUB1_SUPPLY,
                   POE_PD69200_MSG_SUB2_MAIN]
        return self._run_communication_protocol(command, self._msg_delay,
                                                PoeMsgParser.MSG_POWER_SUPPLY_PARAMS)

    def get_port_measurements(self, logic_port):
        command = [POE_PD69200_MSG_KEY_REQUEST,
                   self._calc_msg_echo(),
                   POE_PD69200_MSG_SUB_CHANNEL,
                   POE_PD69200_MSG_SUB1_BT_PORTS_MEASUREMENT,
                   logic_port]
        return self._run_communication_protocol(command, self._msg_delay,
                                                PoeMsgParser.MSG_PORT_MEASUREMENTS)

    def get_poe_versions(self):
        versions = self.get_software_version()
        prod = str(versions.get(PROD_NUM))
        sw_ver = int(versions.get(SW_VERSION))
        major_ver = str(int(sw_ver / 100))
        minor_ver = str(int(sw_ver / 10) % 10)
        pa_ver = str(int(sw_ver % 10))
        return prod + "." + major_ver + "." + minor_ver + "." + pa_ver

    def get_current_power_bank(self):
        params = self.get_power_supply_params()
        return params.get(POWER_BANK)

    def get_poe_port(self, port_id):
        return poePort(self, port_id)

    def get_poe_system(self):
        return poeSystem()

    def get_ports_information(self, portList, more_info=True):
        ports_info = []
        for portidx in portList:
            info = poePort(self, portidx).get_current_status(more_info)
            ports_info.append(info)
        return ports_info

    def get_system_information(self, more_info=True):
        return poeSystem(self).get_current_status(more_info)

class PoeMsgParser(object):
    MSG_PORT_STATUS_PARAMETERS = 1
    MSG_PORT_CLASS = 2
    MSG_POWER_SUPPLY_PARAMS = 3
    MSG_PORT_MEASUREMENTS = 4
    MSG_SYSTEM_STATUS = 5
    MSG_ALL_PORTS_ENDIS = 6
    MSG_POE_DEVICE_STATUS = 7
    MSG_INDV_MASK = 8
    MSG_SW_VERSION = 9

    def _to_word(self, byteH, byteL):
        return (byteH << 8 | byteL) & 0xffff

    def _parse_port_status_parameters(self, msg):
        parsed_data = {
            STATUS: msg[POE_PD69200_MSG_OFFSET_SUB],
            ENDIS: msg[POE_PD69200_MSG_OFFSET_SUB1],
            PROTOCOL: msg[POE_PD69200_MSG_OFFSET_DATA5],
            PRIORITY: msg[POE_PD69200_MSG_OFFSET_DATA7]
        }
        return parsed_data

    def _parse_port_class(self, msg):
        parsed_data = {
            CLASS: msg[POE_PD69200_MSG_OFFSET_DATA8],

            TPPL: self._to_word(msg[POE_PD69200_MSG_OFFSET_DATA9],
                                msg[POE_PD69200_MSG_OFFSET_DATA10])
        }
        return parsed_data

    def _parse_all_ports_endis(self, msg):
        parsed_data = {
            ENDIS: []
        }
        all_ports_endis = [msg[POE_PD69200_MSG_OFFSET_SUB],   # port_7_0
                           msg[POE_PD69200_MSG_OFFSET_SUB1],  # port_15_8
                           msg[POE_PD69200_MSG_OFFSET_SUB2],  # port_23_16
                           msg[POE_PD69200_MSG_OFFSET_DATA5], # port_31_24
                           msg[POE_PD69200_MSG_OFFSET_DATA6], # port_39_32
                           msg[POE_PD69200_MSG_OFFSET_DATA7]] # port_47_40

        for endis_group in all_ports_endis:
            for idx in range(8):
                port_endis = (endis_group >> idx) & 1
                parsed_data[ENDIS].append(port_endis)
        return parsed_data

    def _parse_power_supply_params(self, msg):
        parsed_data = {
            POWER_CONSUMP: self._to_word(msg[POE_PD69200_MSG_OFFSET_SUB],
                                         msg[POE_PD69200_MSG_OFFSET_SUB1]),
            MAX_SD_VOLT: self._to_word(msg[POE_PD69200_MSG_OFFSET_SUB2],
                                       msg[POE_PD69200_MSG_OFFSET_DATA5]),
            MIN_SD_VOLT: self._to_word(msg[POE_PD69200_MSG_OFFSET_DATA6],
                                       msg[POE_PD69200_MSG_OFFSET_DATA7]),
            POWER_BANK: msg[POE_PD69200_MSG_OFFSET_DATA9],
            TOTAL_POWER: self._to_word(msg[POE_PD69200_MSG_OFFSET_DATA10],
                                       msg[POE_PD69200_MSG_OFFSET_DATA11])
        }
        return parsed_data

    def _parse_port_measurements(self, msg):
        parsed_data = {
            CURRENT: self._to_word(msg[POE_PD69200_MSG_OFFSET_SUB2],
                                   msg[POE_PD69200_MSG_OFFSET_DATA5]),
            POWER_CONSUMP: self._to_word(msg[POE_PD69200_MSG_OFFSET_DATA6],
                                         msg[POE_PD69200_MSG_OFFSET_DATA7]),
            VOLTAGE: self._to_word(msg[POE_PD69200_MSG_OFFSET_DATA9],
                                   msg[POE_PD69200_MSG_OFFSET_DATA10])
        }
        return parsed_data

    def _parse_system_status(self, msg):
        parsed_data = {
            CPU_STATUS1: msg[POE_PD69200_MSG_OFFSET_SUB],
            CPU_STATUS2: msg[POE_PD69200_MSG_OFFSET_SUB1],
            FAC_DEFAULT: msg[POE_PD69200_MSG_OFFSET_SUB2],
            GIE: msg[POE_PD69200_MSG_OFFSET_DATA5],
            PRIV_LABEL: msg[POE_PD69200_MSG_OFFSET_DATA6],
            USER_BYTE: msg[POE_PD69200_MSG_OFFSET_DATA7],
            DEVICE_FAIL: msg[POE_PD69200_MSG_OFFSET_DATA8],
            TEMP_DISCO: msg[POE_PD69200_MSG_OFFSET_DATA9],
            TEMP_ALARM: msg[POE_PD69200_MSG_OFFSET_DATA10],
            INTR_REG: self._to_word(msg[POE_PD69200_MSG_OFFSET_DATA11],
                                    msg[POE_PD69200_MSG_OFFSET_DATA12])
        }
        return parsed_data

    def _parse_poe_device_params(self, msg):
        parsed_data = {
            CSNUM: msg[POE_PD69200_MSG_OFFSET_SUB],
            STATUS: msg[POE_PD69200_MSG_OFFSET_DATA5],
            TEMP: msg[POE_PD69200_MSG_OFFSET_DATA9],
            TEMP_ALARM: msg[POE_PD69200_MSG_OFFSET_DATA10]
        }
        return parsed_data

    def _parse_indv_mask(self, msg):
        parsed_data = {
            ENDIS: msg[POE_PD69200_MSG_OFFSET_SUB]
        }
        return parsed_data

    def _parse_sw_version(self, msg):
        parsed_data = {
            PROD_NUM: msg[POE_PD69200_MSG_OFFSET_SUB2],
            SW_VERSION: self._to_word(msg[POE_PD69200_MSG_OFFSET_DATA5],
                                  msg[POE_PD69200_MSG_OFFSET_DATA6])
        }
        return parsed_data

    def parse(self, msg, msg_type):
        if msg_type == self.MSG_PORT_STATUS_PARAMETERS:
            return self._parse_port_status_parameters(msg)
        elif msg_type == self.MSG_PORT_CLASS:
            return self._parse_port_class(msg)
        elif msg_type == self.MSG_POWER_SUPPLY_PARAMS:
            return self._parse_power_supply_params(msg)
        elif msg_type == self.MSG_PORT_MEASUREMENTS:
            return self._parse_port_measurements(msg)
        elif msg_type == self.MSG_SYSTEM_STATUS:
            return self._parse_system_status(msg)
        elif msg_type == self.MSG_ALL_PORTS_ENDIS:
            return self._parse_all_ports_endis(msg)
        elif msg_type == self.MSG_POE_DEVICE_STATUS:
            return self._parse_poe_device_params(msg)
        elif msg_type == self.MSG_INDV_MASK:
            return self._parse_indv_mask(msg)
        elif msg_type == self.MSG_SW_VERSION:
            return self._parse_sw_version(msg)
        return {}

class poePort(object):
    def __init__(self, poe_plat, port_id):
        self.poe_plat = poe_plat
        self.port_id = port_id
        self.enDis = 1
        self.status = ""
        self.priority = ""
        self.protocol = ""
        self.class_type = 0
        self.latch = 0x00
        self.power_consump = 0
        self.power_limit = 0
        self.voltage = 0
        self.current = 0
        # only two pair test
        self.FPairEn = 0

    def update_port_status(self):
        params = self.poe_plat.get_port_parameters(self.port_id)

        self.status = TBL_STATUS_TO_CFG[params.get(STATUS)]
        self.enDis = TBL_ENDIS_TO_CFG[params.get(ENDIS)]
        self.protocol = TBL_PROTOCOL_TO_CFG[params.get(PROTOCOL)]
        self.priority = TBL_PRIORITY_TO_CFG[params.get(PRIORITY)]

        class_power = self.poe_plat.get_port_class(self.port_id)
        port_class = (class_power.get(CLASS) >> 4)
        self.class_type = TBL_CLASS_TO_CFG[port_class]

        meas = self.poe_plat.get_port_measurements(self.port_id)
        self.current = meas.get(CURRENT)
        self.power_consump = meas.get(POWER_CONSUMP)
        self.voltage = meas.get(VOLTAGE)
        self.power_limit = class_power.get(TPPL)

    def get_current_status(self, more_info=True):
        self.update_port_status()
        port_status = OrderedDict()
        port_status[PORT_ID] = self.port_id + 1
        port_status[ENDIS] = self.enDis
        port_status[PRIORITY] = self.priority
        port_status[POWER_LIMIT] = self.power_limit
        if more_info == True:
            port_status[STATUS] = self.status
            port_status[PROTOCOL] = self.protocol
            port_status[LATCH] = self.latch
            port_status[EN_4PAIR] = self.FPairEn
            port_status[CLASS] = self.class_type
            port_status[POWER_CONSUMP] = self.power_consump
            port_status[VOLTAGE] = self.voltage / 10
            port_status[CURRENT] = self.current
        return port_status

    def set_enDis(self, set_val):
        set_flag = False
        status = self.poe_plat.get_port_parameters(self.port_id)
        cur_val = status.get(ENDIS)
        if cur_val != set_val:
            self.poe_plat.set_port_enDis(self.port_id, set_val)
            set_flag = True
        return set_flag

    def set_powerLimit(self, set_val):
        return False

    def set_priority(self, set_val):
        set_flag = False
        priority = self.poe_plat.get_port_parameters(self.port_id)
        cur_val = priority.get(PRIORITY)
        if cur_val != set_val:
            self.poe_plat.set_port_priority(self.port_id, set_val)
            set_flag = True
        return set_flag

    def set_all_params(self, params):
        set_flag = False
        if ENDIS in params:
            set_val = TBL_ENDIS_TO_DRV[params[ENDIS]]
            set_flag |= self.set_enDis(set_val)

        if POWER_LIMIT in params:
            set_val = params[POWER_LIMIT]
            set_flag |= self.set_powerLimit(set_val)

        if PRIORITY in params:
            set_val = TBL_PRIORITY_TO_DRV[params[PRIORITY]]
            set_flag |= self.set_priority(set_val)

        return set_flag

class poeSystem(object):
    def __init__(self, poe_plat):
        self.poe_plat = poe_plat
        self.total_ports = 0
        self.total_power = 0
        self.power_consump = 0
        self.power_avail = 0
        self.power_bank = 0
        self.max_sd_volt = 0
        self.min_sd_volt = 0
        self.power_src = ""
        self.cpu_status1 = 0
        self.cpu_status2 = 0
        self.fac_default = 0
        self.gie = 0
        self.priv_label = 0
        self.user_byte = 0
        self.device_fail = 0
        self.temp_disco = 0
        self.temp_alarm = 0
        self.intr_reg = 0x00

    def update_system_status(self):
        params = self.poe_plat.get_power_supply_params()
        self.total_ports = self.poe_plat.total_poe_port()
        self.total_power = params.get(TOTAL_POWER)
        self.power_consump = params.get(POWER_CONSUMP)
        self.power_avail = self.total_power - self.power_consump
        self.max_sd_volt = params.get(MAX_SD_VOLT)
        self.min_sd_volt = params.get(MIN_SD_VOLT)
        self.power_bank = params.get(POWER_BANK)
        self.power_src = self.poe_plat.bank_to_psu_str(self.power_bank)

        system_status = self.poe_plat.get_system_status()
        self.cpu_status1 = system_status.get(CPU_STATUS1)
        self.cpu_status2 = system_status.get(CPU_STATUS2)
        self.fac_default = system_status.get(FAC_DEFAULT)
        self.gie = system_status.get(GIE)
        self.priv_label = system_status.get(PRIV_LABEL)
        self.user_byte = system_status.get(USER_BYTE)
        self.device_fail = system_status.get(DEVICE_FAIL)
        self.temp_disco = system_status.get(TEMP_DISCO)
        self.temp_alarm = system_status.get(TEMP_ALARM)
        self.intr_reg = system_status.get(INTR_REG)

    def get_current_status(self, more_info=True):
        self.update_system_status()
        system_status = OrderedDict()
        system_status[TOTAL_PORTS] = self.total_ports
        system_status[TOTAL_POWER] = self.total_power
        system_status[POWER_CONSUMP] = self.power_consump
        system_status[POWER_AVAIL] = self.power_avail
        system_status[POWER_BANK] = self.power_bank
        system_status[POWER_SRC] = self.power_src
        if more_info == True:
            system_status[MAX_SD_VOLT] = self.max_sd_volt / 10
            system_status[MIN_SD_VOLT] = self.min_sd_volt / 10
            system_status[CPU_STATUS1] = self.cpu_status1
            system_status[CPU_STATUS2] = self.cpu_status2
            system_status[FAC_DEFAULT] = self.fac_default
            system_status[GIE] = self.gie
            system_status[PRIV_LABEL] = self.priv_label
            system_status[USER_BYTE] = self.user_byte
            system_status[DEVICE_FAIL] = self.device_fail
            system_status[TEMP_DISCO] = self.temp_disco
            system_status[TEMP_ALARM] = self.temp_alarm
            system_status[INTR_REG] = self.intr_reg
        return system_status