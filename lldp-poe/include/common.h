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

#ifndef _LLDP_POE_COMMON_H_
#define _LLDP_POE_COMMON_H_

#include <signal.h>
#include <syslog.h>
/**
 * Get the size of any C array.
 */
#define COUNT_OF(x) (sizeof(x) / sizeof(0 [x]))

/**
 * Iterate in range and iterate for each array element.
 */
#define FOR_I_IN(from, to) for (size_t i = (from); i <= (to); i++)
#define FOR_EACH(item, arr, len)                                               \
    for ((item) = &((arr)[0]); (item) < &((arr)[len]); (item)++)

#define METRICS_FIFO_PATH "/run/poe_helper/poe_metrics_fifo"

#define READ_FIFO_PATH "/run/lldp_poed_read"
#define WRITE_FIFO_PATH "/run/lldp_poed_write"

extern volatile sig_atomic_t thread_exit;

int publish_metrics(const char *metric_name, int metric_value, int port_id);

#endif /* _LLDP_POE_COMMON_H_ */
