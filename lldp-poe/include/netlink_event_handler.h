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

#ifndef _LLDP_POE_NETLINK_EVENT_HANDLER_H_
#define _LLDP_POE_NETLINK_EVENT_HANDLER_H_

#include <sys/socket.h>
#include <sys/types.h>

#include <linux/if.h>
#include <linux/rtnetlink.h>
#include <stdint.h>

/**
 * Maximum number of characters for the prefix size (e.g. "eth" is 4 characters,
 * including the null terminator).
 */
#define IFNAME_PREFIX_SIZE 4U

/**
 * struct port_range - Holds a port range together with the port prefix.
 */
struct port_range {
    char ifname_prefix[IFNAME_PREFIX_SIZE];
    size_t start_index;
    size_t end_index;
};

int netlink_scan_all_ports(struct port_range *);

void *handle_netlink_events();

#endif /* _LLDP_POE_NETLINK_EVENT_HANDLER_H_ */
