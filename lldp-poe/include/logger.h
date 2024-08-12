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

#ifndef _LLDP_POE_LOGGER_H_
#define _LLDP_POE_LOGGER_H_

#include <stdarg.h>
#include <stdio.h>
#include <syslog.h>

#if defined __GNUC__ && __GNUC__ >= 2
#define __func__ __FUNCTION__
#else /* defined __GNUC__ && __GNUC__ >= 2 */
#error Missing platform support for determining the function name.
#endif /* defined __GNUC__ && __GNUC__ >= 2 */

#ifndef LOG_LINE_CHARS
#define LOG_LINE_CHARS 256
#endif /* LOG_LINE_CHARS */

#define POE_CRIT(format, ...)                                                  \
    {                                                                          \
        char buffer[LOG_LINE_CHARS] = {};                                      \
        snprintf(buffer, LOG_LINE_CHARS, format, ##__VA_ARGS__);               \
        syslog(LOG_CRIT, "(%s) %s", __func__, buffer);                         \
    }

#define POE_ERR(format, ...)                                                   \
    {                                                                          \
        char buffer[LOG_LINE_CHARS] = {};                                      \
        snprintf(buffer, LOG_LINE_CHARS, format, ##__VA_ARGS__);               \
        syslog(LOG_ERR, "(%s) %s", __func__, buffer);                          \
    }

#define POE_WARN(format, ...)                                                  \
    {                                                                          \
        char buffer[LOG_LINE_CHARS] = {};                                      \
        snprintf(buffer, LOG_LINE_CHARS, format, ##__VA_ARGS__);               \
        syslog(LOG_WARNING, "(%s) %s", __func__, buffer);                      \
    }

#define POE_NOTICE(format, ...)                                                \
    {                                                                          \
        char buffer[LOG_LINE_CHARS] = {};                                      \
        snprintf(buffer, LOG_LINE_CHARS, format, ##__VA_ARGS__);               \
        syslog(LOG_NOTICE, "(%s) %s", __func__, buffer);                       \
    }

#define POE_INFO(format, ...)                                                  \
    {                                                                          \
        char buffer[LOG_LINE_CHARS] = {};                                      \
        snprintf(buffer, LOG_LINE_CHARS, format, ##__VA_ARGS__);               \
        syslog(LOG_INFO, "(%s) %s", __func__, buffer);                         \
    }

#define POE_DEBUG(format, ...)                                                 \
    {                                                                          \
        char buffer[LOG_LINE_CHARS] = {};                                      \
        snprintf(buffer, LOG_LINE_CHARS, format, ##__VA_ARGS__);               \
        syslog(LOG_DEBUG, "(%s) %s", __func__, buffer);                        \
    }

#define POE_LOG(severity, format, ...)                                         \
    {                                                                          \
        char buffer[LOG_LINE_CHARS] = {};                                      \
        snprintf(buffer, LOG_LINE_CHARS, format, ##__VA_ARGS__);               \
        syslog(severity, "(%s) %s", __func__, buffer);                         \
    }

#endif /* _LLDP_POE_LOGGER_H_ */
