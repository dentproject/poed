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

from poe_common import *
from datetime import datetime, date
from time import sleep
from poe_version import *

import re
import imp
import sys
import subprocess
import os
import argparse
import time
import collections
import json
import pathlib

bootcmd_path   = "/proc/cmdline"
pa_root_path   = os.getcwd() + "/../"
plat_root_path = pa_root_path + "platforms"

PORTLIST_VALIDATION1 = "^([1-9]{0,1}[0-9]{1})-([1-9]{0,1}[0-9]{1})$"
PORTLIST_VALIDATION2 = "^([1-9]{0,1}[0-9]{1})$"

class PoeCLI(object):
    TIME_FMT = "%Y/%m/%d %H:%M:%S"

    def __init__(self):
        self.log = PoeLog()
        self.poe_plat = self.load_poe_platform()

    # Get platform model name from boot cmd
    def platform_model(self, file_path=bootcmd_path):
        try:
            with open(file_path, 'r') as f:
                d = dict(i.split('=') for i in f.read().split(' '))
                return d.get("onl_platform").rstrip()
        except Exception as e:
            print("Failed to get model name from %s. err: %s" % (bootcmd_path, str(e)))
            return "Unknown"

    def platform_src_path(self):
        try:
            # dentOS platform format: <arch>-<manufacturer>-<model>-<revision>
            [arch, manufacturer, model_revision] = self.platform_model().split('-', 2)
            return "/".join([plat_root_path, manufacturer,
                             model_revision, "poe_platform.py"])
        except Exception as e:
            print("Failed to get platform path. err: %s" % str(e))

    def load_poe_platform(self):
        plat_src = imp.load_source("poe_plat", self.platform_src_path())
        poe_plat = plat_src.get_poe_platform()
        return poe_plat

    def valid_ports(self, data):
        portList = []
        total_poe_port = self.poe_plat.total_poe_port()
        try:
            targets = data.split(',')
            re1 = re.compile(PORTLIST_VALIDATION1)
            re2 = re.compile(PORTLIST_VALIDATION2)
            for ports in targets:
                if re1.match(ports):
                    [start, end] = ports.split('-')
                    start = int(start, 0) - 1
                    end = int(end, 0) - 1
                    if end < start:
                        start, end = end, start
                    if start < 0 or end >= total_poe_port:
                        raise ValueError
                    portList += list(range(start, end + 1))
                elif re2.match(ports):
                    port = int(ports, 0) - 1
                    if port < 0 or port >= total_poe_port:
                        raise ValueError
                    portList.append(port)
                else:
                    raise ValueError
            portList = sorted(set(portList))
            return portList
        except ValueError:
            error = "Invalid port inputs: '{0}'.".format(data)
            raise argparse.ArgumentTypeError(error)

    def valid_powerlimit(self, data):
        try:
            power = int(data, 0)
            if 0 <= power <= 0xffff:
                return power
            else:
                raise ValueError
        except ValueError:
            error = "Invalid power limit: '{0}'.".format(data)
            raise argparse.ArgumentTypeError(error)

    def _build_parser(self):
        root_parser = argparse.ArgumentParser()
        root_sub_parser = root_parser.add_subparsers(dest="subcmd",
                                                     help="Descriptions",
                                                     metavar="Commands")

        # Show Sub Command
        show_parser = root_sub_parser.add_parser("show",
                                                 help="Show PoE information",
                                                 formatter_class=argparse.RawTextHelpFormatter)
        show_parser.add_argument("-d", "--debug", action="store_true",
                                 help="Show more Information for debugging\n")
        show_parser.add_argument("-j", "--json", action="store_true",
                                 help="Dump showing information to JSON file\n")
        show_group = show_parser.add_mutually_exclusive_group()
        show_group.add_argument("-p", "--ports", metavar="<val>", type=self.valid_ports,
                                help="Show PoE Ports Information\n"
                                "Example: 1,3-5,45-48")
        show_group.add_argument("-s", "--system", action="store_true",
                                help="Show PoE System Information")
        show_group.add_argument("-m", "--mask", action="store_true",
                                help="Show Individual mask registers")
        show_group.add_argument("-a", "--all", action="store_true",
                                help="Show port, system, and individual masks Information")
        show_group.add_argument("-v", "--version", action="store_true",
                                help="Show PoE versions\n")

        # Set Sub Command
        set_parser = root_sub_parser.add_parser("set", help="Set PoE ports",
                                                formatter_class=argparse.RawTextHelpFormatter)
        set_parser.add_argument("-p", "--ports", metavar="<val>", required=True, type=self.valid_ports,
                                help="Logic ports\n"
                                "Example: 1,3-5,45-48")
        set_parser.add_argument("-e", "--enable", type=lambda x: int(x, 0), choices=[0, 1],
                                metavar="<val>",
                                help="Port Enable/Disable\n"
                                "disable = 0, enable = 1")
        set_parser.add_argument("-l", "--level", type=lambda x: int(x, 0), choices=[1, 2, 3],
                                metavar="<val>",
                                help="Port Priority Level\n"
                                "crit = 1, high = 2, low = 3")
        set_parser.add_argument("-o", "--powerLimit", type=self.valid_powerlimit,
                                metavar="<val>",
                                help="Port Power Limit\n"
                                "range: 0x0 (mW) - 0xffff (mW)\n"
                                "This field will be ignored if val sets to 0xffff")
        # Save Sub Command
        save_parser = root_sub_parser.add_parser("save", help="Save PoE system settings")
        save_parser.add_argument("-s", "--settings", required=True, action="store_true",
                                 help="Save PoE system settings")

        # Restore Sub Command
        resore_parser = root_sub_parser.add_parser("restore",
                                                   help="Restores modified values to factory default values")

        return root_parser

    def json_output(self, data):
        print(json.dumps(data, indent = 4))

    def get_versions(self):
        data = collections.OrderedDict()
        data[SW_VERSION] = self.poe_plat.get_poe_versions()
        data[POE_AGT_VER] = POE_AGENT_VERSION
        data[POE_CFG_VER] = POE_CONFIG_VERSION
        return data

    def get_system_running_state(self):
        return self.poe_plat.get_system_information()

    def get_ports_running_state(self, portList):
        return self.poe_plat.get_ports_information(portList)

    def get_individual_masks(self):
        data = collections.OrderedDict()
        masks = list(range(0x54))
        for mask in masks:
            val = self.poe_plat.get_individual_mask(mask).get(ENDIS)
            key = "0x{:02x}".format(mask)
            data[key] = val
        return data

    def print_poe_version(self, versions):
        print("PoE SW Versions: %s" % versions[SW_VERSION])
        print("PoE Agent Version: %s" % versions[POE_AGT_VER])
        print("PoE Config Version: %s" % versions[POE_CFG_VER])

    def print_ports_information(self, ports_info, debug):
        print("")
        if debug:
            print("Port  Status             En/Dis   Priority  Protocol        Class  PWR Consump  PWR Limit    Voltage    Current   Latch  En4Pair")
            print("----  -----------------  -------  --------  --------------  -----  -----------  -----------  ---------  --------  -----  -------")
        else:
            print("Port  Status             En/Dis   Priority  Protocol        Class  PWR Consump  PWR Limit    Voltage    Current ")
            print("----  -----------------  -------  --------  --------------  -----  -----------  -----------  ---------  --------")
        for info in ports_info:
            if debug:
                output = "{:<4d}  {:17s}  {:7s}  {:^8s}  {:14s}  {:^5s}  {:6d} (mW)  {:6d} (mW)  {:5.1f} (V)  {:3d} (mA)  {:5s}  {:4d}".format(
                         info.get(PORT_ID), info.get(STATUS), info.get(ENDIS),
                         info.get(PRIORITY), info.get(PROTOCOL), info.get(CLASS),
                         info.get(POWER_CONSUMP), info.get(POWER_LIMIT), info.get(VOLTAGE),
                         info.get(CURRENT), "0x{:02x}".format(info.get(LATCH)), info.get(EN_4PAIR))
            else:
                output = "{:<4d}  {:17s}  {:7s}  {:^8s}  {:14s}  {:^5s}  {:6d} (mW)  {:6d} (mW)  {:5.1f} (V)  {:3d} (mA)".format(
                         info.get(PORT_ID), info.get(STATUS), info.get(ENDIS),
                         info.get(PRIORITY), info.get(PROTOCOL), info.get(CLASS),
                         info.get(POWER_CONSUMP), info.get(POWER_LIMIT), info.get(VOLTAGE),
                         info.get(CURRENT))
            print(output)
        print("")

    def print_system_information(self, system_info, debug):
        print("")
        print("==============================")
        print(" PoE System Information")
        print("==============================")
        print(" Total PoE Ports   : %d" % system_info.get(TOTAL_PORTS))
        print("")
        print(" Total Power       : %.1f W" % system_info.get(TOTAL_POWER))
        print(" Power Consumption : %.1f W" % system_info.get(POWER_CONSUMP))
        print(" Power Avaliable   : %.1f W" % system_info.get(POWER_AVAIL))
        print("")
        print(" Power Bank #      : %d" % system_info.get(POWER_BANK))
        print(" Power Sources     : %s" % system_info.get(POWER_SRC))
        print("")
        if debug:
            print(" Max Shutdown Volt : %.1f V" % system_info.get(MAX_SD_VOLT))
            print(" Min Shutdown Volt : %.1f V" % system_info.get(MIN_SD_VOLT))
            print("")
            print(" PM1               : 0x%02x" % system_info.get(PM1))
            print(" PM2               : 0x%02x" % system_info.get(PM2))
            print(" PM3               : 0x%02x" % system_info.get(PM3))
            print("")
            print(" CPU Status1       : 0x%02x" % system_info.get(CPU_STATUS1))
            print(" CPU Status2       : 0x%02x" % system_info.get(CPU_STATUS2))
            print(" FAC Default       : %d"     % system_info.get(FAC_DEFAULT))
            print(" General Intl Err  : 0x%02x" % system_info.get(GIE))
            print(" Private Label     : 0x%02x" % system_info.get(PRIV_LABEL))
            print(" User Byte         : 0x%02x" % system_info.get(USER_BYTE))
            print(" Device Fail       : 0x%02x" % system_info.get(DEVICE_FAIL))
            print(" Temp Disconnect   : 0x%02x" % system_info.get(TEMP_DISCO))
            print(" Temp Alarm        : 0x%02x" % system_info.get(TEMP_ALARM))
            print(" Interrupt Reg     : 0x%04x" % system_info.get(INTR_REG))
            print("")

    def print_indv_masks(self, masks):
        print("")
        print("==================")
        print(" Individual Masks")
        print("==================")
        for key in masks:
            print(" {:s}:{:2d}".format(key, masks[key]))
        print("")

    @PoeAccessExclusiveLock
    def show_versions(self, json):
        try:
            data = collections.OrderedDict()
            data[VERSIONS] = self.get_versions()
            if json:
                self.json_output(data)
            else:
                self.print_poe_version(data[VERSIONS])
        except Exception as e:
            print("Failed to show poe versions! (%s)" % str(e))

    @PoeAccessExclusiveLock
    def show_system_information(self, debug, json):
        try:
            data = collections.OrderedDict()
            data[SYS_INFO] = self.get_system_running_state()
            if json:
                self.json_output(data)
            else:
                self.print_system_information(data[SYS_INFO], debug)
        except Exception as e:
            print("Failed to show poe system information! (%s)" % str(e))

    @PoeAccessExclusiveLock
    def show_ports_information(self, portList, debug, json):
        try:
            data = collections.OrderedDict()
            data[PORT_INFO] = self.get_ports_running_state(portList)
            if json:
                self.json_output(data)
            else:
                self.print_ports_information(data[PORT_INFO], debug)
        except Exception as e:
            print("Failed to show poe ports information! (%s)" % str(e))

    @PoeAccessExclusiveLock
    def show_individual_masks(self, json):
        try:
            data = collections.OrderedDict()
            data[INDV_MASKS] = self.get_individual_masks()
            if json:
                self.json_output(data)
            else:
                self.print_indv_masks(data[INDV_MASKS])
        except Exception as e:
            print("Failed to show individual masks! (%s)" % str(e))

    @PoeAccessExclusiveLock
    def show_all_information(self, debug, json):
        try:
            portList = list(range(self.poe_plat.total_poe_port()))
            data = collections.OrderedDict()
            data[VERSIONS] = self.get_versions()
            data[SYS_INFO] = self.get_system_running_state()
            data[PORT_INFO] = self.get_ports_running_state(portList)
            data[INDV_MASKS] = self.get_individual_masks()
            if json:
                self.json_output(data)
            else:
                self.print_poe_version(data[VERSIONS])
                self.print_system_information(data[SYS_INFO], debug)
                self.print_ports_information(data[PORT_INFO], debug)
                self.print_indv_masks(data[INDV_MASKS])
        except Exception as e:
            print("Failed to show all information! (%s)" % str(e))

    @PoeAccessExclusiveLock
    def set_ports_enDis(self, portList, val):
        try:
            for port_id in portList:
                poe_port = self.poe_plat.get_poe_port(port_id)
                poe_port.set_enDis(val)
            return True
        except Exception as e:
            print("Failed to set ports enable/disable! (%s)" % str(e))
        return False

    @PoeAccessExclusiveLock
    def set_ports_powerLimit(self, portList, val):
        try:
            for port_id in portList:
                poe_port = self.poe_plat.get_poe_port(port_id)
                poe_port.set_powerLimit(val)
            return True
        except Exception as e:
            print("Failed to set ports power limit! (%s)" % str(e))
        return False

    @PoeAccessExclusiveLock
    def set_ports_priority(self, portList, val):
        try:
            for port_id in portList:
                poe_port = self.poe_plat.get_poe_port(port_id)
                poe_port.set_priority(val)
            return True
        except Exception as e:
            print("Failed to set ports priority! (%s)" % str(e))
        return False

    @PoeAccessExclusiveLock
    def save_system_settings(self):
        try:
            self.poe_plat.save_system_settings()
        except Exception as e:
            print("Failed to save poe system settings! (%s)" % str(e))

    @PoeAccessExclusiveLock
    def restore_factory_default(self):
        try:
            self.poe_plat.restore_factory_default()
            self.poe_plat.init_poe()
            print("Success to restore factory default and take platform poe settings!")
        except Exception as e:
            print("Failed to restore factory default! (%s)" % str(e))

    def get_current_time(self):
        return datetime.now().strftime(self.TIME_FMT)

    def is_poed_alive(self):
        try:
            pid = int(open(POED_PID_PATH, 'r').read())
            os.kill(pid, 0)
        except OSError:
            return False
        else:
            return True

    def send_ipc_event(self, action=POECLI_SET):
        try:
            with open(POE_IPC_EVT, "w") as f:
                f.write(action)
        except Exception as e:
            pass

def main(argv):
    try:
        poecli = PoeCLI()
    except Exception as e:
        raise RuntimeError("Failed to load poe platform! (%s)" % str(e))

    parser = poecli._build_parser()
    args = parser.parse_args()

    set_flag = False
    if args.subcmd == "show":
        if (args.ports is None and args.system is False and \
            args.all is False and args.mask is False and args.version is False):
            parser.error("No action requested for %s command" % args.subcmd)

        debug_flag = args.debug
        json_flag = args.json
        if args.ports:
            poecli.show_ports_information(args.ports, debug_flag, json_flag)
        elif args.system:
            poecli.show_system_information(debug_flag, json_flag)
        elif args.mask:
            poecli.show_individual_masks(json_flag)
        elif args.all:
            poecli.show_all_information(debug_flag, json_flag)
        elif args.version:
            poecli.show_versions(json_flag)
    elif args.subcmd == "set":
        if (args.enable is None and args.level is None and args.powerLimit is None):
            parser.error("No action requested for %s command" % args.subcmd)
        if args.enable is not None:
            set_flag |= poecli.set_ports_enDis(args.ports, args.enable)
        if args.level is not None:
            set_flag |= poecli.set_ports_priority(args.ports, args.level)
        if args.powerLimit is not None:
            set_flag |= poecli.set_ports_powerLimit(args.ports, args.powerLimit)
    elif args.subcmd == "save":
        if not args.settings:
            parser.error("No action requested for %s command" % args.subcmd)
        poecli.save_system_settings()
    elif args.subcmd == "restore":
        poecli.restore_factory_default()
        set_flag = True

    if set_flag == True and poecli.is_poed_alive() == True:
        poecli.send_ipc_event()

if __name__ == '__main__':
    try:
        main(sys.argv)
    finally:
        exit(0)

