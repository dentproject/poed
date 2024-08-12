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

#ifndef _LLDP_POE_LLDP_EVENT_HANDLER_H_
#define _LLDP_POE_LLDP_EVENT_HANDLER_H_

#include <stdbool.h>

#include "port_state_machine.h"

int send_mdi_pse_advertisement(const char *,
                               const struct port_dot3_power_settings *,
                               time_t *);

bool is_neighbor_already_reconciled(const char *);

void *handle_lldp_events();

#endif /* _LLDP_POE_LLDP_EVENT_HANDLER_H_ */
