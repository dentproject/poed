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

#include <errno.h>
#include <fcntl.h>
#include <string.h>
#include <unistd.h>

#include "cJSON/cJSON.h"
#include "include/common.h"
#include "include/lldp_poed_err.h"
#include "include/logger.h"

/**
 * publish_metrics - Send a metric formatted as JSON through
 * the metrics FIFO
 * @metric_name: the metric key used for identification
 * @metric_value: the metric value
 * @port_id: port id associated to the metric. Should be set to 0 if no port.
 *
 * Returns 0, LLDP_POED_ERR_OK, if successful, an lldp_poed_err otherwise.
 */
int publish_metrics(const char *metric_name, int metric_value, int port_id)
{
    return LLDP_POED_ERR_INTERNAL_ERROR;
}