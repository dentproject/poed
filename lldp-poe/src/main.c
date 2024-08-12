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
#include <pthread.h>
#include <signal.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define SYSLOG_NAMES
#include "include/logger.h"
#include "include/common.h"
#include "include/lldp_event_handler.h"
#include "include/lldp_poed_err.h"
#include "include/netlink_event_handler.h"
#include "include/port_state_machine.h"

#define LOG_LEVEL_UNKNOWN "UNKNOWN"

/**
 * struct thread_details - Hold the thread details required for starting a
 * thread and waiting for a thread to finish.
 */
struct thread_details {
    pthread_t thread_id;
    char *thread_name;
    void *(*thread_handler_fn)(void *);
};

/**
 * Convenience macro for handling a pthread call error.
 */
#define HANDLE_PTHREAD_ERR(op, thread_name, msg)                               \
    do {                                                                       \
        POE_CRIT("Failed to " op " %s: %s (errno = %s)", thread_name, msg,     \
                 strerror(errno));                                             \
        exit(EXIT_FAILURE);                                                    \
    } while (0)

/**
 * Add a new thread to the initializer list.
 */
#define INITIALIZE_NEW_PTHREAD(handler_fn)                                     \
    {                                                                          \
        .thread_name = #handler_fn, .thread_handler_fn = handler_fn,           \
    }

/**
 * Global thread exit flag.
 */
volatile sig_atomic_t thread_exit = 0;

/**
 * exit_threads - Sets the global thread flag to true.
 */
void exit_threads(int sig_num)
{
    POE_DEBUG("Exited via signal %d", sig_num);
    thread_exit = sig_num;
}

/**
 * Assign an initial logging level.
 */
int log_level = LOG_WARNING;

/**
 * get_loglevel - finds log level name in prioritynames struct from syslog.
 * @loglevel_number: the priority number of log level.
 * 
 * returns the log level name of the log level prio number.
 * returns UNKNOWN in case the prio number is not correct.
 */
char* loglevel_to_string(int loglevel_number)
{
    int i;
    static const int length = sizeof(prioritynames) / sizeof(CODE);
    for (i = 0; i < length; i++) {
        if (prioritynames[i].c_val == loglevel_number) {
            return prioritynames[i].c_name;
        }
    }
    syslog(LOG_ERR, "Changing log level to unknown level name using prio number (%d).", loglevel_number);
    return LOG_LEVEL_UNKNOWN;
}

/**
 * Signal handler to increase log level with SIGUSR1 and decrease it with SIGUSR2.
 */
void change_loglevel(int sig_num)
{
    int loglevel_before_change = log_level;
    bool log_changed = false;
    if (sig_num == SIGUSR1) {        
        if (log_level < LOG_DEBUG) {
            log_level += 1;
            log_changed = true;
        }
    } else if (sig_num == SIGUSR2) {
        if (log_level > LOG_EMERG) {
            log_level -= 1;
            log_changed = true;
        }
    }
    /**
     * Set new log level when a change has happened.
     */
    if (log_changed) {
        setlogmask(LOG_UPTO(log_level));
        syslog(log_level, "Log level changed from (%d)(%s) to (%d)(%s).", loglevel_before_change, loglevel_to_string(loglevel_before_change), log_level, loglevel_to_string(log_level));
    }
}

/**
 * init_log - Set up logging through syslog. 
 */
void init_logging()
{
    setlogmask(LOG_UPTO(log_level));
    openlog("lldp-poed", LOG_CONS | LOG_PID | LOG_NDELAY, LOG_DAEMON);
    /**
     * Handle log level changes.
     */
    signal(SIGUSR1, change_loglevel);
    signal(SIGUSR2, change_loglevel);
}

int main()
{
    /**
     * Handle both SIGTERM and SIGSEGV.
     */
    signal(SIGTERM, exit_threads);
    signal(SIGSEGV, exit_threads);

    init_logging();

    /**
     * The threads that are going to be created, in the given order.
     */
    struct thread_details threads[] = {
        INITIALIZE_NEW_PTHREAD(handle_port_state_machine),
        INITIALIZE_NEW_PTHREAD(handle_netlink_events),
        INITIALIZE_NEW_PTHREAD(handle_lldp_events),
    };

    struct thread_details *thread_it = NULL;
    FOR_EACH(thread_it, threads, COUNT_OF(threads))
    {
        int status = pthread_create(&(thread_it->thread_id), NULL,
                                    thread_it->thread_handler_fn, NULL);
        if (0 != status)
            HANDLE_PTHREAD_ERR("create", thread_it->thread_name,
                               strerror(errno));
        POE_DEBUG("Created %s thread", thread_it->thread_name);
    }

    /* Wait for all threads to finish. */
    FOR_EACH(thread_it, threads, COUNT_OF(threads))
    {
        int status = pthread_join(thread_it->thread_id, NULL);
        if (0 != status)
            HANDLE_PTHREAD_ERR("join", thread_it->thread_name, strerror(errno));
        POE_DEBUG("Thread %s successfully joined", thread_it->thread_name);
    }

    publish_metrics("lldp_poed_exit", thread_exit, 0);
    closelog();

    return 0;
}
