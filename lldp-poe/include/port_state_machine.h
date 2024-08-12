/**
 * Copyright Amazon Inc.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *    http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
*/

#ifndef _LLDP_POE_PORT_STATE_MACHINE_H_
#define _LLDP_POE_PORT_STATE_MACHINE_H_

#include <stdint.h>

/**
 * We support only ints and map the ID to the port array index.
 * However, in the future this will allow using a
 * different identifier for finding the port (e.g. the MAC address).
 */
typedef int port_id_t;

/**
 * struct port_dot3_power_settings - Container for Dot3 power information
 * received from the LLDP neighbor and for sending MDI advertisements
 *
 * For details regarding each field, please consult lldpctl.h and
 * lldpd-structs.h. The fields are used as defined in the IEEE 802.3bt standard.
 */
struct port_dot3_power_settings {
    /**
     * 802.1ab and 802.3at fields.
     */
    uint8_t poe_device_type;
    uint8_t mdi_supported;
    uint8_t mdi_enabled;
    uint8_t mdi_paircontrol;
    uint8_t pse_power_pair;
    uint8_t pd_class;
    uint8_t power_type;
    uint8_t power_source;
    uint8_t power_priority;
    uint16_t pd_requested;
    uint16_t pse_allocated;

    /**
     * 802.3bt additions for Type 3 and Type 4 devices.
     */
    uint8_t pd_4pid;
    uint16_t pd_requested_a;
    uint16_t pd_requested_b;
    uint16_t pse_allocated_a;
    uint16_t pse_allocated_b;
    uint16_t pse_status;
    uint8_t pd_status;
    uint8_t pse_pairs_ext;
    uint8_t power_class_mode_a;
    uint8_t power_class_mode_b;
    uint8_t pd_power_class_ext;
    uint8_t power_type_ext;
    uint8_t pd_load;
    uint16_t pse_max_available_power;
};

/**
 * struct port_med_power_settings - Container for MED power information
 * received from the LLDP neighbor and for sending MDI advertisements
 *
 * For details regarding each field, please consult lldpctl.h and
 * lldpd-structs.h. The fields are used as defined in the ANSI/TIA-1057 standard.
 */
struct port_med_power_settings {
    uint8_t poe_device_type;
    uint8_t power_source;
    uint8_t power_priority;
    uint16_t value;
};

int med_to_dot3(const struct port_med_power_settings *med_config,
                struct port_dot3_power_settings *dot3_config);

int dot3_to_med(const struct port_dot3_power_settings* dot3_config,
                struct port_med_power_settings* med_config);

int push_lldp_neighbor_update(const char *,
                              const struct port_dot3_power_settings *);

/**
 * enum port_if_link_event - Relevant link change events for the port state
 * machine
 * @PORT_IF_UP: interface is enabled an has an active data link
 * @PORT_IF_DOWN: interface is operationally down
 */
enum port_if_link_event {
    PORT_IF_UP,
    PORT_IF_DOWN,
};

int push_if_link_update(const char *, enum port_if_link_event);

void *handle_port_state_machine();

#endif /* _LLDP_POE_PORT_STATE_MACHINE_H_ */
