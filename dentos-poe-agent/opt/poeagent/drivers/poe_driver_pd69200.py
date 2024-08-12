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

import time
import sys
import os
import json
from collections import OrderedDict
from poe_common import *
from poe_driver_pd69200_def import *

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
    _last_send_key = None

    def __init__(self):
        self._echo = 0x00
        self._4wire_bt = 0
        # Time between commands: 30ms
        self._msg_delay = 0.03
        # Wait time after saving system setting: 50ms
        self._save_sys_delay = 0.05
        # Wait time after restore factory default setting: 100ms
        self._restore_factory_default_delay = 0.1
        # Wait time to clear up poe chip I2C buffer: 500ms
        self._clear_bus_buffer_delay = 0.5
        # Wake up time delay after reset poe chip command: 300ms
        self._reset_poe_chip_delay = 0.3

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
                               Tx key is %02x, Rx key should be %02x, but received %02x" %
                               (tx_key, POE_PD69200_MSG_KEY_REPORT, rx_key))
        if tx_key == POE_PD69200_MSG_KEY_REQUEST and rx_key != POE_PD69200_MSG_KEY_TELEMETRY:
            raise RuntimeError("Key field in Tx/Rx message is mismatch,\
                               Tx key is %02x, Rx key should be %02x, but received %02x" %
                               (tx_key, POE_PD69200_MSG_KEY_TELEMETRY, rx_key))

        tx_echo, rx_echo = tx_msg[POE_PD69200_MSG_OFFSET_ECHO], rx_msg[POE_PD69200_MSG_OFFSET_ECHO]
        if rx_echo != tx_echo:
            raise RuntimeError("Echo field in Tx/Rx message is mismatch,\
                               Tx Echo is %02x, Rx Echo is %02x" % (tx_echo, rx_echo))

        csum = self._calc_msg_csum(rx_msg[0:POE_PD69200_MSG_OFFSET_CSUM_H])
        if (rx_msg[POE_PD69200_MSG_OFFSET_CSUM_H] != csum[0] or
                rx_msg[POE_PD69200_MSG_OFFSET_CSUM_L] != csum[1]):
            raise RuntimeError("Invalid checksum in POE Rx message")


    @PoeCommExclusiveLock()
    def _communicate(self, tx_msg, delay):
        retry = 0
        ex="Unknown"
        while retry < POE_PD69200_COMM_RETRY_TIMES:
            try:
                self._xmit(tx_msg, delay)
                if retry > 0:
                    print_stderr("Send(retry: {0}): {1}".format(retry, conv_byte_to_hex(tx_msg)))
                rx_msg = self._recv()
                self._check_rx_msg(rx_msg, tx_msg)
                return rx_msg
            except Exception as e:
                ex = e
                print_stderr("_communicate error: {0}".format(str(e)))
                print_stderr("Send: {0}".format(conv_byte_to_hex(tx_msg)))
                print_stderr("Recv: {0}".format(conv_byte_to_hex(rx_msg)))
                rx_msg = self._recv()
                print_stderr("Recv(clean buffer): {0}".format(conv_byte_to_hex(rx_msg)))
                # Increment echo byte
                command = tx_msg[0:POE_PD69200_MSG_OFFSET_DATA12]
                command[POE_PD69200_MSG_OFFSET_ECHO] = self._calc_msg_echo()
                tx_msg = self._build_tx_msg(command)
                # Wait 0.5s to clear up I2C buffer
                time.sleep(self._clear_bus_buffer_delay)
                retry += 1
        raise RuntimeError(
            "Problems in running poe communication protocol: {0}".format(str(ex)))

    def _run_communication_protocol(self, command, delay, msg_type=None):
        tx_msg = self._build_tx_msg(command)
        if self._last_send_key == tx_msg[POE_PD69200_MSG_OFFSET_KEY] and \
                tx_msg[POE_PD69200_MSG_OFFSET_KEY] == POE_PD69200_MSG_KEY_COMMAND:
            time.sleep(self._msg_delay)
        rx_msg = self._communicate(tx_msg, delay)
        self._last_send_key = tx_msg[POE_PD69200_MSG_OFFSET_KEY]
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
        return self._run_communication_protocol(command, self._reset_poe_chip_delay,
                                                PoeMsgParser.MSG_CMD_STATUS)

    def restore_factory_default(self):
        command = [POE_PD69200_MSG_KEY_PROGRAM,
                   self._calc_msg_echo(),
                   POE_PD69200_MSG_SUB_RESOTRE_FACT]
        return self._run_communication_protocol(command, self._restore_factory_default_delay,
                                                PoeMsgParser.MSG_CMD_STATUS)

    def save_system_settings(self):
        command = [POE_PD69200_MSG_KEY_PROGRAM,
                   self._calc_msg_echo(),
                   POE_PD69200_MSG_SUB_E2,
                   POE_PD69200_MSG_SUB1_SAVE_CONFIG]
        return self._run_communication_protocol(command, self._save_sys_delay,
                                                PoeMsgParser.MSG_CMD_STATUS)

    def set_user_byte_to_save(self, user_val):
        command = [POE_PD69200_MSG_KEY_PROGRAM,
                   self._calc_msg_echo(),
                   POE_PD69200_MSG_SUB_USER_BYTE,
                   user_val]
        return self._run_communication_protocol(command, self._save_sys_delay,
                                                PoeMsgParser.MSG_CMD_STATUS)

    # System status function
    def set_system_status(self, priv_label):
        command = [POE_PD69200_MSG_KEY_COMMAND,
                   self._calc_msg_echo(),
                   POE_PD69200_MSG_SUB_GLOBAL,
                   POE_PD69200_MSG_SUB1_SYSTEM_STATUS,
                   priv_label]
        return self._run_communication_protocol(command, self._msg_delay,
                                                PoeMsgParser.MSG_CMD_STATUS)

    def get_system_status(self):
        command = [POE_PD69200_MSG_KEY_REQUEST,
                   self._calc_msg_echo(),
                   POE_PD69200_MSG_SUB_GLOBAL,
                   POE_PD69200_MSG_SUB1_SYSTEM_STATUS]
        return self._run_communication_protocol(command, self._msg_delay,
                                                PoeMsgParser.MSG_SYSTEM_STATUS)

    def get_bt_system_status(self):
        command = [POE_PD69200_MSG_KEY_REQUEST,
                   self._calc_msg_echo(),
                   POE_PD69200_MSG_SUB_GLOBAL,
                   POE_PD69200_BT_MSG_SUB1_SYSTEM_STATUS]
        return self._run_communication_protocol(command, self._msg_delay,
                                                PoeMsgParser.MSG_BT_SYSTEM_STATUS)

    def set_individual_mask(self, mask_num, enDis):
        command = [POE_PD69200_MSG_KEY_COMMAND,
                   self._calc_msg_echo(),
                   POE_PD69200_MSG_SUB_GLOBAL,
                   POE_PD69200_MSG_SUB1_INDV_MSK,
                   mask_num,
                   enDis]
        return self._run_communication_protocol(command, self._msg_delay,
                                                PoeMsgParser.MSG_CMD_STATUS)

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

    def support_4wire_bt(self, min_major_ver=3):
        poe_ver = self.get_poe_versions()
        major_ver = int(poe_ver.split('.')[1])
        if major_ver >= min_major_ver:
            self._4wire_bt = 1
            return 1
        else:
            self._4wire_bt = 0
            return 0

    def set_temp_matrix(self, logic_port, phy_port_a, phy_port_b=0xFF):
        command = [POE_PD69200_MSG_KEY_COMMAND,
                   self._calc_msg_echo(),
                   POE_PD69200_MSG_SUB_CHANNEL,
                   POE_PD69200_MSG_SUB1_TEMP_MATRIX,
                   logic_port, phy_port_a, phy_port_b]
        return self._run_communication_protocol(command, self._msg_delay,
                                                PoeMsgParser.MSG_CMD_STATUS)

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
        return self._run_communication_protocol(command, self._msg_delay,
                                                PoeMsgParser.MSG_CMD_STATUS)

    def get_active_matrix(self, logic_port):
        command = [POE_PD69200_MSG_KEY_REQUEST,
                   self._calc_msg_echo(),
                   POE_PD69200_MSG_SUB_CHANNEL,
                   POE_PD69200_MSG_SUB1_CH_MATRIX,
                   logic_port]
        return self._run_communication_protocol(command, self._msg_delay,
                                                PoeMsgParser.MSG_ACTIVE_MATRIX)

    def set_port_enDis(self, logic_port, EnDis):
        command = [POE_PD69200_MSG_KEY_COMMAND,
                   self._calc_msg_echo(),
                   POE_PD69200_MSG_SUB_CHANNEL,
                   POE_PD69200_MSG_SUB1_EN_DIS,
                   logic_port,
                   POE_PD69200_MSG_DATA_CMD_ENDIS_ONLY | EnDis,
                   POE_PD69200_MSG_DATA_PORT_TYPE_AT]
        return self._run_communication_protocol(command, self._msg_delay,
                                                PoeMsgParser.MSG_CMD_STATUS)

    def set_bt_port_enDis(self, logic_port, EnDis):
        command = [POE_PD69200_MSG_KEY_COMMAND,
                   self._calc_msg_echo(),
                   POE_PD69200_MSG_SUB_CHANNEL,
                   POE_PD69200_BT_MSG_SUB1_PORTS_PARAMETERS,
                   logic_port,
                   POE_PD69200_MSG_DATA_CMD_ENDIS_ONLY | EnDis,
                   POE_PD69200_BT_MSG_DATA_PORT_MODE_NO_CHANGE | POE_PD69200_BT_MSG_DATA_PORT_CLASS_ERROR_NO_CHANGE,
                   POE_PD69200_BT_MSG_DATA_PORT_OP_MODE_NO_CHANGE,
                   POE_PD69200_BT_MSG_DATA_PORT_MODE_POWER_SAME,
                   POE_PD69200_BT_MSG_DATA_PORT_PRIORITY_NO_CHANGE]
        return self._run_communication_protocol(command, self._msg_delay,
                                                PoeMsgParser.MSG_CMD_STATUS)

    def get_all_ports_enDis(self):
        # Only support AT/AF Protocol, to speedup setting flow
        if self._4wire_bt == 0:
            command = [POE_PD69200_MSG_KEY_REQUEST,
                    self._calc_msg_echo(),
                    POE_PD69200_MSG_SUB_GLOBAL,
                    POE_PD69200_MSG_SUB1_EN_DIS]
            return self._run_communication_protocol(command, self._msg_delay,
                                                    PoeMsgParser.MSG_ALL_PORTS_ENDIS)
        else:
            # Skip Get All port command
            return None

    all_ports_enDis = property(get_all_ports_enDis, None)

    # logic_port range: 0x00 to 0x2F, 'AllChannels' = 0x80
    def set_port_power_limit(self, logic_port, power_limit):
        command = [POE_PD69200_MSG_KEY_COMMAND,
                   self._calc_msg_echo(),
                   POE_PD69200_MSG_SUB_CHANNEL,
                   POE_PD69200_MSG_SUB1_SUPPLY,
                   logic_port,
                   power_limit >> 8,
                   power_limit & 0xff]
        return self._run_communication_protocol(command, self._msg_delay,
                                                PoeMsgParser.MSG_CMD_STATUS)

    def get_port_power_limit(self, logic_port):
        command = [POE_PD69200_MSG_KEY_REQUEST,
                   self._calc_msg_echo(),
                   POE_PD69200_MSG_SUB_CHANNEL,
                   POE_PD69200_MSG_SUB1_SUPPLY,
                   logic_port]
        return self._run_communication_protocol(command, self._msg_delay,
                                                PoeMsgParser.MSG_PORT_POWER_LIMIT)

    def set_port_priority(self, logic_port, priority):
        command = [POE_PD69200_MSG_KEY_COMMAND,
                   self._calc_msg_echo(),
                   POE_PD69200_MSG_SUB_CHANNEL,
                   POE_PD69200_MSG_SUB1_PRIORITY,
                   logic_port,
                   priority]
        return self._run_communication_protocol(command, self._msg_delay,
                                                PoeMsgParser.MSG_CMD_STATUS)

    def set_bt_port_priority(self, logic_port, priority):
        command = [POE_PD69200_MSG_KEY_COMMAND,
                   self._calc_msg_echo(),
                   POE_PD69200_MSG_SUB_CHANNEL,
                   POE_PD69200_BT_MSG_SUB1_PORTS_PARAMETERS,
                   logic_port,
                   POE_PD69200_BT_MSG_DATA_CMD_ENDIS_NO_CHAGNE,
                   POE_PD69200_BT_MSG_DATA_PORT_MODE_NO_CHANGE | POE_PD69200_BT_MSG_DATA_PORT_CLASS_ERROR_NO_CHANGE,
                   POE_PD69200_BT_MSG_DATA_PORT_OP_MODE_NO_CHANGE,
                   POE_PD69200_BT_MSG_DATA_PORT_MODE_POWER_SAME,
                   priority]
        return self._run_communication_protocol(command, self._msg_delay,
                                                PoeMsgParser.MSG_CMD_STATUS)

    def get_port_priority(self, logic_port):
        command = [POE_PD69200_MSG_KEY_REQUEST,
                   self._calc_msg_echo(),
                   POE_PD69200_MSG_SUB_CHANNEL,
                   POE_PD69200_MSG_SUB1_PRIORITY,
                   logic_port]
        return self._run_communication_protocol(command, self._msg_delay,
                                                PoeMsgParser.MSG_PORT_PRIORITY)

    def get_port_status(self, logic_port):
        command = [POE_PD69200_MSG_KEY_REQUEST,
                   self._calc_msg_echo(),
                   POE_PD69200_MSG_SUB_CHANNEL,
                   POE_PD69200_MSG_SUB1_PORT_STATUS,
                   logic_port]
        return self._run_communication_protocol(command, self._msg_delay,
                                                PoeMsgParser.MSG_PORT_STATUS)

    def set_pm_method(self, pm1, pm2, pm3):
        command = [POE_PD69200_MSG_KEY_COMMAND,
                   self._calc_msg_echo(),
                   POE_PD69200_MSG_SUB_GLOBAL,
                   POE_PD69200_MSG_SUB1_SUPPLY,
                   POE_PD69200_MSG_SUB2_PWR_MANAGE_MODE,
                   pm1, pm2, pm3]
        return self._run_communication_protocol(command, self._msg_delay,
                                                PoeMsgParser.MSG_CMD_STATUS)

    def get_pm_method(self):
        command = [POE_PD69200_MSG_KEY_REQUEST,
                   self._calc_msg_echo(),
                   POE_PD69200_MSG_SUB_GLOBAL,
                   POE_PD69200_MSG_SUB1_SUPPLY,
                   POE_PD69200_MSG_SUB2_PWR_MANAGE_MODE]
        return self._run_communication_protocol(command, self._msg_delay,
                                                PoeMsgParser.MSG_PM_METHOD)

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
                   bank, ]
        command += [x for x in int(power_limit).to_bytes(2, byteorder="big")]
        command += [x for x in int(self._max_shutdown_vol).to_bytes(2,byteorder="big")]
        command += [x for x in int(self._min_shutdown_vol).to_bytes(2,byteorder="big")]
        command.append(self._guard_band)
        return self._run_communication_protocol(command, self._msg_delay,
                                                PoeMsgParser.MSG_CMD_STATUS)

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
                   POE_PD69200_MSG_SUB1_PARAMZ,
                   logic_port]
        return self._run_communication_protocol(command, self._msg_delay,
                                                PoeMsgParser.MSG_PORT_MEASUREMENTS)

    def get_bt_port_measurements(self, logic_port):
        command = [POE_PD69200_MSG_KEY_REQUEST,
                   self._calc_msg_echo(),
                   POE_PD69200_MSG_SUB_CHANNEL,
                   POE_PD69200_BT_MSG_SUB1_PORTS_MEASUREMENT,
                   logic_port]
        return self._run_communication_protocol(command, self._msg_delay,
                                                PoeMsgParser.MSG_BT_PORT_MEASUREMENTS)

    def get_poe_device_parameters(self, csnum):
        command = [POE_PD69200_MSG_KEY_REQUEST,
                   self._calc_msg_echo(),
                   POE_PD69200_MSG_SUB_GLOBAL,
                   POE_PD69200_MSG_SUB1_DEV_PARAMS,
                   csnum]
        return self._run_communication_protocol(command, self._msg_delay,
                                                PoeMsgParser.MSG_POE_DEVICE_STATUS)

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

    def get_bt_port_parameters(self, logic_port):
        command = [POE_PD69200_MSG_KEY_REQUEST,
                   self._calc_msg_echo(),
                   POE_PD69200_MSG_SUB_CHANNEL,
                   POE_PD69200_BT_MSG_SUB1_PORTS_PARAMETERS,
                   logic_port]
        return self._run_communication_protocol(command, self._msg_delay,
                                                PoeMsgParser.MSG_BT_PORT_PARAMETERS)

    def get_bt_port_class(self, logic_port):
        command = [POE_PD69200_MSG_KEY_REQUEST,
                   self._calc_msg_echo(),
                   POE_PD69200_MSG_SUB_CHANNEL,
                   POE_PD69200_BT_MSG_SUB1_PORTS_CLASS,
                   logic_port]
        return self._run_communication_protocol(command, self._msg_delay,
                                                PoeMsgParser.MSG_BT_PORT_CLASS)

    def set_bt_port_operation_mode(self, logic_port, mode):
        command = [POE_PD69200_MSG_KEY_COMMAND,
                   self._calc_msg_echo(),
                   POE_PD69200_MSG_SUB_CHANNEL,
                   POE_PD69200_BT_MSG_SUB1_PORTS_PARAMETERS,
                   logic_port,
                   POE_PD69200_BT_MSG_DATA_CMD_ENDIS_NO_CHAGNE,
                   POE_PD69200_BT_MSG_DATA_PORT_MODE_NO_CHANGE | POE_PD69200_BT_MSG_DATA_PORT_CLASS_ERROR_NO_CHANGE,
                   mode,
                   POE_PD69200_BT_MSG_DATA_PORT_MODE_POWER_SAME,
                   POE_PD69200_BT_MSG_DATA_PORT_PRIORITY_NO_CHANGE]
        return self._run_communication_protocol(command, self._msg_delay,
                                                PoeMsgParser.MSG_CMD_STATUS)


class PoeMsgParser(object):
    MSG_PORT_POWER_LIMIT = 1
    MSG_PORT_PRIORITY = 2
    MSG_PORT_STATUS = 3
    MSG_POWER_SUPPLY_PARAMS = 4
    MSG_PORT_MEASUREMENTS = 5
    MSG_SYSTEM_STATUS = 6
    MSG_ALL_PORTS_ENDIS = 7
    MSG_POE_DEVICE_STATUS = 8
    MSG_INDV_MASK = 9
    MSG_PM_METHOD = 10
    MSG_SW_VERSION = 11
    MSG_BT_PORT_MEASUREMENTS = 12
    MSG_BT_PORT_PARAMETERS = 13
    MSG_BT_SYSTEM_STATUS = 14
    MSG_BT_PORT_CLASS = 15
    MSG_ACTIVE_MATRIX = 16
    MSG_CMD_STATUS = 255

    def _to_word(self, byteH, byteL):
        return (byteH << 8 | byteL) & 0xffff

    def _parse_port_power_limit(self, msg):
        parsed_data = {
            PPL: self._to_word(msg[POE_PD69200_MSG_OFFSET_SUB],
                               msg[POE_PD69200_MSG_OFFSET_SUB1]),
            TPPL: self._to_word(msg[POE_PD69200_MSG_OFFSET_SUB2],
                                msg[POE_PD69200_MSG_OFFSET_DATA5])
        }
        return parsed_data

    def _parse_port_priority(self, msg):
        parsed_data = {
            PRIORITY: msg[POE_PD69200_MSG_OFFSET_SUB]
        }
        return parsed_data

    def _parse_port_status(self, msg):
        parsed_data = {
            ENDIS: msg[POE_PD69200_MSG_OFFSET_SUB],
            STATUS: msg[POE_PD69200_MSG_OFFSET_SUB1],
            LATCH: msg[POE_PD69200_MSG_OFFSET_DATA5],
            CLASS: msg[POE_PD69200_MSG_OFFSET_DATA6],
            PROTOCOL: msg[POE_PD69200_MSG_OFFSET_DATA10],
            EN_4PAIR: msg[POE_PD69200_MSG_OFFSET_DATA11]
        }
        return parsed_data

    def _parse_bt_port_status_parameters(self, msg):
        parsed_data = {
            STATUS: msg[POE_PD69200_MSG_OFFSET_SUB],
            ENDIS: msg[POE_PD69200_MSG_OFFSET_SUB1],
            OPERATION_MODE: msg[POE_PD69200_MSG_OFFSET_DATA5],
            PRIORITY: msg[POE_PD69200_MSG_OFFSET_DATA7]
        }
        return parsed_data

    def _parse_all_ports_endis(self, msg):
        parsed_data = {
            ENDIS: []
        }
        all_ports_endis = [msg[POE_PD69200_MSG_OFFSET_SUB],   # port_7_0
                           msg[POE_PD69200_MSG_OFFSET_SUB1],  # port_15_8
                           msg[POE_PD69200_MSG_OFFSET_SUB2],  # port_23_16
                           msg[POE_PD69200_MSG_OFFSET_DATA6], # port_31_24
                           msg[POE_PD69200_MSG_OFFSET_DATA7], # port_39_32
                           msg[POE_PD69200_MSG_OFFSET_DATA8]] # port_47_40

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

    def _parse_bt_port_measurements(self, msg):
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

    def _parse_bt_system_status(self, msg):
        parsed_data = {
            CPU_STATUS2: msg[POE_PD69200_MSG_OFFSET_SUB1],
            FAC_DEFAULT: msg[POE_PD69200_MSG_OFFSET_SUB2],
            PRIV_LABEL: msg[POE_PD69200_MSG_OFFSET_DATA6],
            NVM_USER_BYTE: msg[POE_PD69200_MSG_OFFSET_DATA7],
            FOUND_DEVICE: msg[POE_PD69200_MSG_OFFSET_DATA8],
            EVENT_EXIST: msg[POE_PD69200_MSG_OFFSET_DATA12]
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

    def _parse_pm_method(self, msg):
        parsed_data = {
            PM1: msg[POE_PD69200_MSG_OFFSET_SUB],
            PM2: msg[POE_PD69200_MSG_OFFSET_SUB1],
            PM3: msg[POE_PD69200_MSG_OFFSET_SUB2]
        }
        return parsed_data

    def _parse_sw_version(self, msg):
        parsed_data = {
            PROD_NUM: msg[POE_PD69200_MSG_OFFSET_SUB2],
            SW_VERSION: self._to_word(msg[POE_PD69200_MSG_OFFSET_DATA5],
                                      msg[POE_PD69200_MSG_OFFSET_DATA6])
        }
        return parsed_data

    def _parse_bt_port_class(self, msg):
        parsed_data = {
            MEASURED_CLASS: msg[POE_PD69200_MSG_OFFSET_SUB2],
            CLASS: msg[POE_PD69200_MSG_OFFSET_DATA8],
            TPPL: self._to_word(msg[POE_PD69200_MSG_OFFSET_DATA9],
                                msg[POE_PD69200_MSG_OFFSET_DATA10])
        }
        return parsed_data

    def _parse_cmd_status(self, msg):
        parsed_data = int.from_bytes(bytes(
            [msg[POE_PD69200_MSG_OFFSET_SUB], msg[POE_PD69200_MSG_OFFSET_SUB1]]), byteorder='big')
        return parsed_data

    def _parse_active_matrix(self, msg):
        parsed_data = {
            ACTIVE_MATRIX_PHYA: msg[POE_PD69200_MSG_OFFSET_SUB],
            ACTIVE_MATRIX_PHYB: msg[POE_PD69200_MSG_OFFSET_SUB1],

        }
        return parsed_data

    def parse(self, msg, msg_type):
        if msg_type == self.MSG_PORT_POWER_LIMIT:
            return self._parse_port_power_limit(msg)
        elif msg_type == self.MSG_PORT_PRIORITY:
            return self._parse_port_priority(msg)
        elif msg_type == self.MSG_PORT_STATUS:
            return self._parse_port_status(msg)
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
        elif msg_type == self.MSG_PM_METHOD:
            return self._parse_pm_method(msg)
        elif msg_type == self.MSG_SW_VERSION:
            return self._parse_sw_version(msg)
        elif msg_type == self.MSG_BT_PORT_PARAMETERS:
            return self._parse_bt_port_status_parameters(msg)
        elif msg_type == self.MSG_BT_PORT_CLASS:
            return self._parse_bt_port_class(msg)
        elif msg_type == self.MSG_BT_SYSTEM_STATUS:
            return self._parse_bt_system_status(msg)
        elif msg_type == self.MSG_BT_PORT_MEASUREMENTS:
            return self._parse_bt_port_measurements(msg)
        elif msg_type == self.MSG_ACTIVE_MATRIX:
            return self._parse_active_matrix(msg)
        elif msg_type == self.MSG_CMD_STATUS:
            return self._parse_cmd_status(msg)
        return {}

class poePort(object):
    def __init__(self, poe_plat, port_id):
        self.poe_plat = poe_plat
        self.port_id = port_id
        self.enDis = 1
        self.status = ""
        self.priority = ""
        self.protocol = ""
        self.latch = 0x00
        self.class_type = 0
        self.FPairEn = 0
        self.power_consump = 0
        self.power_limit = 0
        self.voltage = 0
        self.current = 0
        self.measured_class = 0
        self._4wire_bt = self.poe_plat._4wire_bt

    def update_port_status(self):
        if self._4wire_bt == 1:
            params = self.poe_plat.get_bt_port_parameters(self.port_id)
            params_class = self.poe_plat.get_bt_port_class(self.port_id)
            self.status = TBL_BT_STATUS_TO_CFG[params.get(STATUS)]
            self.enDis = TBL_ENDIS_TO_CFG[params.get(ENDIS)]
            self.measured_class = params_class.get(MEASURED_CLASS) >> 4
            # Delivers power, port status: 0x80-0x91
            if params.get(STATUS) >= 0x80 and params.get(STATUS) <= 0x91:
                if self.measured_class >= 0 and self.measured_class <= 4:
                    self.protocol = "IEEE802.3AF/AT"
                elif self.measured_class >= 5 and self.measured_class <= 8:
                    self.protocol = "IEEE802.3BT"
                else:
                    self.protocol = "NA"
            else:
                self.protocol = "NA"

            self.priority = TBL_PRIORITY_TO_CFG[params.get(PRIORITY)]

            power_limit = self.poe_plat.get_bt_port_class(self.port_id)
            port_class = (power_limit.get(CLASS) >> 4)
            self.class_type = TBL_BT_CLASS_TO_CFG[port_class]
            self.power_limit = power_limit.get(TPPL)

            meas = self.poe_plat.get_bt_port_measurements(self.port_id)
            self.current = meas.get(CURRENT)
            self.power_consump = meas.get(POWER_CONSUMP)
            self.voltage = meas.get(VOLTAGE)
        else:
            status = self.poe_plat.get_port_status(self.port_id)
            self.enDis = TBL_ENDIS_TO_CFG[status.get(ENDIS)]
            self.status = TBL_STATUS_TO_CFG[status.get(STATUS)]
            self.latch = status.get(LATCH)
            self.class_type = TBL_CLASS_TO_CFG[status.get(CLASS)]
            self.protocol = TBL_PROTOCOL_TO_CFG[status.get(PROTOCOL)]
            self.FPairEn = status.get(EN_4PAIR)

            priority = self.poe_plat.get_port_priority(self.port_id)
            self.priority = TBL_PRIORITY_TO_CFG[priority.get(PRIORITY)]

            power_limit = self.poe_plat.get_port_power_limit(self.port_id)
            self.power_limit = power_limit.get(PPL)

            meas = self.poe_plat.get_port_measurements(self.port_id)
            self.current = meas.get(CURRENT)
            self.power_consump = meas.get(POWER_CONSUMP)
            self.voltage = meas.get(VOLTAGE)

    def get_current_status(self, more_info=True):
        self.update_port_status()
        port_status = OrderedDict()
        if self._4wire_bt == 1:
            port_status[PORT_ID] = self.port_id + 1
            port_status[ENDIS] = self.enDis
            port_status[PRIORITY] = self.priority
            port_status[POWER_LIMIT] = self.power_limit * 100
            if more_info == True:
                port_status[STATUS] = self.status
                port_status[PROTOCOL] = self.protocol
                port_status[LATCH] = self.latch
                port_status[EN_4PAIR] = self.FPairEn
                port_status[CLASS] = self.class_type
                port_status[POWER_CONSUMP] = self.power_consump * 100
                port_status[VOLTAGE] = self.voltage / 10
                port_status[CURRENT] = self.current
        else:
            port_status[PORT_ID] = self.port_id + 1
            port_status[ENDIS] = self.enDis
            port_status[PRIORITY] = self.priority
            port_status[POWER_LIMIT] = self.power_limit
            if more_info == True:
                port_status[STATUS] = self.status
                port_status[LATCH] = self.latch
                port_status[PROTOCOL] = self.protocol
                port_status[EN_4PAIR] = self.FPairEn
                port_status[CLASS] = self.class_type
                port_status[POWER_CONSUMP] = self.power_consump
                port_status[VOLTAGE] = self.voltage / 10
                port_status[CURRENT] = self.current

        return port_status

    def set_enDis(self, set_val, current_enDis=None, check_Readback=False):
        ret_flag = 0
        result_get = set_val
        if current_enDis != None and ENDIS in current_enDis and \
            self.port_id <= (len(current_enDis[ENDIS])-1) and \
                current_enDis[ENDIS][self.port_id] == set_val:
            # print_stderr(
            #     "current_enDis[{0}] match: get: {1}/set: {2}, skip.".format(str(self.port_id), str(current_enDis[ENDIS][self.port_id]), str(set_val)))
            return ret_flag
        else:
            if self._4wire_bt == 1:
                result = self.poe_plat.set_bt_port_enDis(self.port_id, set_val)
                if check_Readback:
                    result_get = self.poe_plat.get_bt_port_parameters(self.port_id).get(ENDIS)
                if result == 0 or result_get == set_val:
                    ret_flag = result
            else:
                result = self.poe_plat.set_port_enDis(self.port_id, set_val)
                if check_Readback:
                    result_get = self.poe_plat.get_port_status(
                        self.port_id).get(ENDIS)
                if result == 0 or result_get == set_val:
                    ret_flag = result

            return ret_flag

    def set_powerLimit(self, set_val, check_Readback=False):
        ret_flag = 0
        result_get = set_val
        if self._4wire_bt == 1:
            raise RuntimeError("Not support on BT firmware")
        else:
            result = self.poe_plat.set_port_power_limit(self.port_id, set_val)
            if check_Readback:
                result_get = self.poe_plat.get_port_power_limit(
                    self.port_id).get(PPL)
            if result == 0 or result_get == set_val:
                ret_flag = result
        return ret_flag

    def set_priority(self, set_val, check_Readback=False):
        ret_flag = 0
        result_get = set_val
        if self._4wire_bt == 1:
            result = self.poe_plat.set_bt_port_priority(self.port_id, set_val)
            if check_Readback:
                result_get = self.poe_plat.get_bt_port_parameters(
                    self.port_id).get(PRIORITY)
            if result == 0 or result_get == set_val:
                ret_flag = result
        else:
            result = self.poe_plat.set_port_priority(self.port_id, set_val)
            if check_Readback:
                result_get = self.poe_plat.get_port_priority(
                    self.port_id).get(PRIORITY)
            if result == 0 or result_get == set_val:
                ret_flag = result
        return ret_flag

    def set_all_params(self, params, current_enDis=None, check_Readback=False):
        ret_flag = dict({})

        if ENDIS in params:
            set_val = TBL_ENDIS_TO_DRV[params[ENDIS]]
            ret_flag[ENDIS] = self.set_enDis(
                set_val, current_enDis, check_Readback)

        if PRIORITY in params:
            set_val = TBL_PRIORITY_TO_DRV[params[PRIORITY]]
            ret_flag[PRIORITY] = self.set_priority(set_val, check_Readback)

        if POWER_LIMIT in params and self._4wire_bt != 1:
            set_val = params[POWER_LIMIT]
            ret_flag[POWER_LIMIT] = self.set_powerLimit(
                set_val, check_Readback)

        return ret_flag

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
        self.pm1 = 0
        self.pm2 = 0
        self.pm3 = 0
        self.nvm_user_byte = 0
        self.found_device = 0
        self.event_exist = 0
        self._4wire_bt = self.poe_plat._4wire_bt

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
        if self._4wire_bt == 1:
            system_status = self.poe_plat.get_bt_system_status()
            self.cpu_status2 = system_status.get(CPU_STATUS2)
            self.fac_default = system_status.get(FAC_DEFAULT)
            self.priv_label = system_status.get(PRIV_LABEL)
            self.nvm_user_byte = system_status.get(NVM_USER_BYTE)
            self.found_device = system_status.get(FOUND_DEVICE)
            self.event_exist = system_status.get(EVENT_EXIST)
        else:
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

            pm_method = self.poe_plat.get_pm_method()
            self.pm1 = pm_method.get(PM1)
            self.pm2 = pm_method.get(PM2)
            self.pm3 = pm_method.get(PM3)

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
            system_status[PM1] = self.pm1
            system_status[PM2] = self.pm2
            system_status[PM3] = self.pm3
            system_status[CPU_STATUS1] = self.cpu_status1
            # cpu status2 on AT and BT
            system_status[CPU_STATUS2] = self.cpu_status2
            system_status[FAC_DEFAULT] = self.fac_default
            system_status[GIE] = self.gie
            system_status[PRIV_LABEL] = self.priv_label
            system_status[USER_BYTE] = self.user_byte
            system_status[DEVICE_FAIL] = self.device_fail
            system_status[TEMP_DISCO] = self.temp_disco
            system_status[TEMP_ALARM] = self.temp_alarm
            system_status[INTR_REG] = self.intr_reg
            # only on BT
            system_status[NVM_USER_BYTE] = self.nvm_user_byte
            system_status[FOUND_DEVICE] = self.found_device
            system_status[EVENT_EXIST] = self.event_exist
        return system_status
