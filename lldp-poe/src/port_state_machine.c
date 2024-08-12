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
#include <limits.h>
#include <linux/rtnetlink.h>
#include <lldp-const.h>
#include <poll.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <time.h>
#include <unistd.h>

#include "include/common.h"
#include "include/lldp_event_handler.h"
#include "include/lldp_poed_err.h"
#include "include/logger.h"
#include "include/netlink_event_handler.h"
#include "include/payload.h"
#include "include/port_state_machine.h"
#include "include/queue.h"

/**
 * Prefix used for constructing the whole interface name (e.g. eth0).
 * Note: this is platform-specific,
 */
#ifndef PORT_INTERFACE_NAME_PREFIX
#define PORT_INTERFACE_NAME_PREFIX "swp"
#endif /* PORT_INTERFACE_NAME_PREFIX */

#ifndef POED_MESSAGE_MAX_SIZE
#define POED_MESSAGE_MAX_SIZE 1024U
#endif /* POED_MESSAGE_MAX_SIZE */

/**
 * enum port_state - States a port may go through during the L2 negotiation,
 * starting with PORT_UNINIT
 * @PORT_INVALID_STATE: invalid state as a consequence of an illegal
 * state machine transition
 * @PORT_UNINIT: default starting state
 * @PORT_DISABLED: port disabled by the user for PoE
 * @PORT_FAULT: denied operation due to an internal hardware error
 * @PORT_WAIT_PD: port is enabled and waiting for a PD to connect
 * @PORT_L1_NEG_COMPLETE: L1 negotiation completed successfully
 * Preparing to send the initial power advertisement as a PSE
 * @PORT_WAIT_LLDP_REQ: Dot3 PoE-MDI advertisement was sent successfully
 * Waiting for a valid PD PoE power request
 * @PORT_DEFAULT_PWR_LIMIT: the port was assigned a default power
 * limit due to not receiving any PoE request from the neighbor or failing to
 * apply the power configuration
 * @PORT_L2_NEG_COMPLETE: received a valid PoE-MDI power request from the PD
 * and was able to reconcile it and adjust the power budget, using
 * the neighbor data
 * @PORT_LOST_POWER_LINK: lost the PD physical link
 * @PORT_STATE_MAX: total number of port states
 *
 * A port state should be advanced only by a result of calling the handler
 * and checking the state and event against the lookup table.
 */
enum port_state {
    PORT_INVALID_STATE = 0,
    PORT_UNINIT,
    PORT_DISABLED,
    PORT_FAULT,
    PORT_WAIT_PD,
    PORT_L1_NEG_COMPLETE,
    PORT_WAIT_LLDP_REQ,
    PORT_DEFAULT_PWR_LIMIT,
    PORT_L2_NEG_COMPLETE,
    PORT_LOST_POWER_LINK,
    PORT_STATE_MAX,
};

/**
 * enum port_state_event - Events that may trigger a port state change
 * @PORT_EVENT_PORT_ENABLED: the port was enabled for PoE operation
 * @PORT_EVENT_PORT_DISABLED: detected that the port got disabled
 * @PORT_EVENT_LOST_POWER: detected that both the data link and the physical
 * to the PD got lost. This means that the L1 and L2 negotiation have to be
 * reinitiated
 * @PORT_EVENT_LLDP_RESTORE: restore the Dot3 PoE data from the neighbor
 * information, if it already exists
 * @PORT_EVENT_LLDP_TIMEOUT: there was no valid PoE-MDI advertisement received
 * within the holdtime window (aka TTL)
 * @PORT_EVENT_OK: operation was successful. This can mean, for example, that
 * an incoming PoE request was reconciled successfully
 * @PORT_EVENT_ERR: port operation failed. Either a driver request failed or
 * an LLDP request wasn't fulfilled
 * @PORT_EVENT_IDLE: no change is requested
 * @PORT_EVENT_MAX: total number of port events
 *
 * Some states may require executing a single command, while other states
 * may require listening for a certain event. Either way, all states
 * must have a handler defined.
 */
enum port_state_event {
    PORT_EVENT_PORT_ENABLED = 0,
    PORT_EVENT_PORT_DISABLED,
    PORT_EVENT_LOST_POWER,
    PORT_EVENT_LLDP_RESTORE,
    PORT_EVENT_LLDP_TIMEOUT,
    PORT_EVENT_OK,
    PORT_EVENT_ERR,
    PORT_EVENT_IDLE, /* No transition. */
    PORT_EVENT_MAX,
};

/**
 * port_state_string - Reverse lookup table for stringifying the pot state
 */
const char *port_state_string[PORT_STATE_MAX] = {
    "PORT_INVALID_STATE",   "PORT_UNINIT",
    "PORT_DISABLED",        "PORT_FAULT",
    "PORT_WAIT_PD",         "PORT_L1_NEG_COMPLETE",
    "PORT_WAIT_LLDP_REQ",   "PORT_DEFAULT_PWR_LIMIT",
    "PORT_L2_NEG_COMPLETE", "PORT_LOST_POWER_LINK",
};

/**
 * port_transition_table - Lookup table for all possible state transitions
 * depending on the current port state and the given port event (PORT_EVENT_IDLE
 * can be skipped and handled the same way for all states, hence the minus 1).
 * By indexing through a state-event combination, the caller can determine the
 * next state in which to move a port in or if it's an illegal transition.
 */
static enum port_state
    port_transition_table[PORT_STATE_MAX][PORT_EVENT_MAX - 1] = {
        /**
         * PORT_INVALID_STATE - No road to take from here.
         */
        {
            PORT_INVALID_STATE,
            PORT_INVALID_STATE,
            PORT_INVALID_STATE,
            PORT_INVALID_STATE,
            PORT_INVALID_STATE,
            PORT_INVALID_STATE,
            PORT_INVALID_STATE,
        },
        /**
         * PORT_UNINIT
         */
        {
            PORT_INVALID_STATE,
            PORT_INVALID_STATE,
            PORT_INVALID_STATE,
            PORT_INVALID_STATE,
            PORT_INVALID_STATE,
            PORT_INVALID_STATE,
            PORT_INVALID_STATE,
        },
        /**
         * PORT_DISABLED
         */
        {
            PORT_INVALID_STATE,
            PORT_INVALID_STATE,
            PORT_INVALID_STATE,
            PORT_INVALID_STATE,
            PORT_INVALID_STATE,
            PORT_INVALID_STATE,
            PORT_INVALID_STATE,
        },
        /**
         * PORT_FAULT
         */
        {
            PORT_INVALID_STATE,
            PORT_INVALID_STATE,
            PORT_INVALID_STATE,
            PORT_INVALID_STATE,
            PORT_INVALID_STATE,
            PORT_INVALID_STATE,
            PORT_INVALID_STATE,
        },
        /**
         * PORT_WAIT_PD
         */
        {
            PORT_INVALID_STATE,
            PORT_INVALID_STATE,
            PORT_INVALID_STATE,
            PORT_INVALID_STATE,
            PORT_INVALID_STATE,
            PORT_INVALID_STATE,
            PORT_INVALID_STATE,
        },
        /**
         * PORT_L1_NEG_COMPLETE
         */
        {
            PORT_INVALID_STATE,
            PORT_INVALID_STATE,
            PORT_INVALID_STATE,
            PORT_INVALID_STATE,
            PORT_INVALID_STATE,
            PORT_INVALID_STATE,
            PORT_INVALID_STATE,
        },
        /**
         * PORT_WAIT_LLDP_REQ
         */
        {
            PORT_INVALID_STATE,
            PORT_INVALID_STATE,
            PORT_INVALID_STATE,
            PORT_INVALID_STATE,
            PORT_INVALID_STATE,
            PORT_INVALID_STATE,
            PORT_INVALID_STATE,
        },
        /**
         * PORT_DEFAULT_PWR_LIMIT
         */
        {
            PORT_INVALID_STATE,
            PORT_INVALID_STATE,
            PORT_INVALID_STATE,
            PORT_INVALID_STATE,
            PORT_INVALID_STATE,
            PORT_INVALID_STATE,
            PORT_INVALID_STATE,
        },
        /**
         * PORT_L2_NEG_COMPLETE
         */
        {
            PORT_INVALID_STATE,
            PORT_INVALID_STATE,
            PORT_INVALID_STATE,
            PORT_INVALID_STATE,
            PORT_INVALID_STATE,
            PORT_INVALID_STATE,
            PORT_INVALID_STATE,
        },
        /**
         * PORT_LOST_POWER_LINK
         */
        {
            PORT_INVALID_STATE,
            PORT_INVALID_STATE,
            PORT_INVALID_STATE,
            PORT_INVALID_STATE,
            PORT_INVALID_STATE,
            PORT_INVALID_STATE,
            PORT_INVALID_STATE,
        },
};

/**
 * init_transition_table - Assign the valid state transitions, based on the port
 * event
 */
static void init_transition_table(void)
{
    /**
     * PORT_UNINIT
     */
    port_transition_table[PORT_UNINIT][PORT_EVENT_PORT_ENABLED] = PORT_WAIT_PD;
    port_transition_table[PORT_UNINIT][PORT_EVENT_PORT_DISABLED] =
        PORT_DISABLED;
    port_transition_table[PORT_UNINIT][PORT_EVENT_ERR] = PORT_FAULT;

    /**
     * PORT_DISABLED
     */
    port_transition_table[PORT_DISABLED][PORT_EVENT_PORT_ENABLED] =
        PORT_WAIT_PD;
    port_transition_table[PORT_DISABLED][PORT_EVENT_ERR] = PORT_FAULT;

    /**
     * PORT_FAULT
     */
    port_transition_table[PORT_FAULT][PORT_EVENT_PORT_ENABLED] = PORT_WAIT_PD;
    port_transition_table[PORT_FAULT][PORT_EVENT_PORT_DISABLED] = PORT_DISABLED;

    /**
     * PORT_WAIT_PD
     */
    port_transition_table[PORT_WAIT_PD][PORT_EVENT_PORT_DISABLED] =
        PORT_DISABLED;
    port_transition_table[PORT_WAIT_PD][PORT_EVENT_LLDP_RESTORE] =
        PORT_L2_NEG_COMPLETE;
    port_transition_table[PORT_WAIT_PD][PORT_EVENT_OK] = PORT_L1_NEG_COMPLETE;
    port_transition_table[PORT_WAIT_PD][PORT_EVENT_ERR] = PORT_FAULT;

    /**
     * PORT_L1_NEG_COMPLETE
     */
    port_transition_table[PORT_L1_NEG_COMPLETE][PORT_EVENT_LOST_POWER] =
        PORT_LOST_POWER_LINK;
    port_transition_table[PORT_L1_NEG_COMPLETE][PORT_EVENT_OK] =
        PORT_WAIT_LLDP_REQ;
    port_transition_table[PORT_L1_NEG_COMPLETE][PORT_EVENT_ERR] =
        PORT_DEFAULT_PWR_LIMIT; /* Couldn't advertise the MDI support,
                                      therefore this entails falling back to the
                                      default power limit.  */

    /**
     * PORT_WAIT_LLDP_REQ
     */
    port_transition_table[PORT_WAIT_LLDP_REQ][PORT_EVENT_LOST_POWER] =
        PORT_LOST_POWER_LINK;
    port_transition_table[PORT_WAIT_LLDP_REQ][PORT_EVENT_LLDP_TIMEOUT] =
        PORT_DEFAULT_PWR_LIMIT;
    port_transition_table[PORT_WAIT_LLDP_REQ][PORT_EVENT_OK] =
        PORT_L2_NEG_COMPLETE;

    /**
     * PORT_DEFAULT_PWR_LIMIT
     */
    port_transition_table[PORT_DEFAULT_PWR_LIMIT][PORT_EVENT_LOST_POWER] =
        PORT_LOST_POWER_LINK;
    port_transition_table[PORT_DEFAULT_PWR_LIMIT][PORT_EVENT_OK] =
        PORT_L2_NEG_COMPLETE; /* Received a valid PD request after all. */

    /**
     * PORT_L2_NEG_COMPLETE
     */
    port_transition_table[PORT_L2_NEG_COMPLETE][PORT_EVENT_LOST_POWER] =
        PORT_LOST_POWER_LINK;
    port_transition_table[PORT_L2_NEG_COMPLETE][PORT_EVENT_ERR] =
        PORT_L1_NEG_COMPLETE; /* This will allow PDs to change the power
                                 allocation by reinitiating the L2 negotiation,
                                 after aging out. */

    /**
     * PORT_LOST_POWER_LINK
     */
    port_transition_table[PORT_LOST_POWER_LINK][PORT_EVENT_OK] = PORT_UNINIT;
}

/**
 * State handler prototype, specific to each state.
 * Returns a port event which may determine a state transition.
 */
struct port_state_machine;
typedef enum port_state_event (*state_handler_fn_t)(struct port_state_machine *,
                                                    const void *);

/**
 * struct port_state_machine - Port state machine binding
 * @id: ID used to identify the port
 * @ifname: network interface name
 * @admin_lldp_enabled: lldp processing enable/disable flag
 * @if_up: interface operational status
 * @timeout_time: future timestamp when the MDI advertisement expires (nullable)
 * @current_state: current port state
 * @process_state: state handler to be called in order to generate a
 * port_state_event
 */
struct port_state_machine {
    port_id_t id;
    char ifname[IFNAMSIZ];
    bool admin_lldp_enabled;
    bool lldp_default_pwr_limit_update_pending;
    bool if_up;
    time_t timeout_time;
    enum port_state current_state;
    state_handler_fn_t process_state;
};

/**
 * struct port_neighbor_update - Container used for queueing up LLDP
 * neighbor updates to be processed in the state machine
 * @id: ID used to identify the port
 * @settings: LLDP Dot3 port config
 * @was_deleted: neighbor deleted flag
 *
 * @warning: the LLDP neighbor count must be limited to 1 to avoid undefined PoE
 * behavior.
 */
struct port_neighbor_update {
    port_id_t id;
    struct port_dot3_power_settings settings;
    bool was_deleted;
};

/**
 * struct port_array - Non-resizable port array
 *
 * The structure should be initialized upon querying the total number of ports
 * available on the device.
 */
static struct port_array {
    size_t size;
    struct port_state_machine *p;
} ports = {
    .size = 0,
    .p = NULL,
};

/**
 * get_port_context_by_id - Find a port, given its ID
 * @id: port ID
 * @port: valid pointer to be used for referencing the port
 *
 * Returns 0 if the port is found, 1 otherwise.
 *
 * @warning: the port index may be different than what is returned by
 * if_nametoindex(). Therefore, events must be reported through the interface
 * name, not the index.
 */
static int get_port_context_by_id(const port_id_t id,
                                  struct port_state_machine **port)
{
    /**
     * Account for the one-indexing in the port map.
     */
    if (!port || id <= 0 || id > ports.size || !ports.size)
        return 1;
    if (id != ports.p[id - 1].id)
        return 1;

    *port = &(ports.p[id - 1]);
    return 0;
}

/**
 * get_port_context_by_ifname - Find a port, given its Linux interface name
 * @name: port interface name
 * @port: valid pointer to be used for referencing the port
 *
 * Returns 0 if the port is found, 1 otherwise.
 */
static int get_port_context_by_ifname(const char *ifname,
                                      struct port_state_machine **port)
{
    if (!port || !ifname || !ports.size)
        return 1;

    int status = 1;
    struct port_state_machine *port_it = NULL;
    FOR_EACH(port_it, ports.p, ports.size)
    {
        if (0 == strncmp(port_it->ifname, ifname, IFNAMSIZ)) {
            *port = port_it;
            status = 0;
            break;
        }
    }

    return status;
}

/* State handlers begin */

static int wait_for_poed_response(char *message, size_t message_len);
static int sync_send_poed_request(struct poed_payload *query,
                                  const char *method);

/**
 * create_get_port_details_query - Populate the poed_payload fields for the
 * get_port_details method
 * @id: port ID
 *
 * @warning: caller has the responsibility to free the payload memory.
 *
 * Returns the newly created payload.
 */
static struct poed_payload *create_get_port_details_query(port_id_t id)
{
    struct poed_payload *params = malloc(sizeof(struct poed_payload));
    struct poed_payload *port_query = malloc(sizeof(struct poed_payload));
    if (!params || !port_query)
        return NULL;

    strncpy(params->name, "port_id", PAYLOAD_NAME_MAX_SIZE);
    params->type = PAYLOAD_VALUE_NUMBER;
    params->value.val_int = id;
    strncpy(port_query->name, "params", PAYLOAD_NAME_MAX_SIZE);
    port_query->type = PAYLOAD_VALUE_OBJECT;
    port_query->child_count = 1;
    port_query->children = params;

    return port_query;
}

/**
 * Convenience macro for declaring a state handler.
 */
#define DECLARE_STATE_HANDLER(state, fn_name)                                  \
    static enum port_state_event fn_name(struct port_state_machine *port,      \
                                         const void *data)

DECLARE_STATE_HANDLER(PORT_INVALID_STATE, process_invalid_state);
DECLARE_STATE_HANDLER(PORT_UNINIT, process_uninit_state);
DECLARE_STATE_HANDLER(PORT_DISABLED, process_disabled_state);
DECLARE_STATE_HANDLER(PORT_FAULT, process_fault_state);
DECLARE_STATE_HANDLER(PORT_WAIT_PD, process_wait_pd_state);
DECLARE_STATE_HANDLER(PORT_L1_NEG_COMPLETE, process_l1_neg_complete_state);
DECLARE_STATE_HANDLER(PORT_WAIT_LLDP_REQ, process_wait_lldp_req_state);
DECLARE_STATE_HANDLER(PORT_DEFAULT_PWR_LIMIT, process_default_pwr_limit_state);
DECLARE_STATE_HANDLER(PORT_L2_NEG_COMPLETE, process_l2_neg_complete_state);
DECLARE_STATE_HANDLER(PORT_LOST_POWER_LINK, process_lost_power_link_state);

/**
 * state_handlers - Handler to port state mapping
 *
 * Each state has a unique handler attached to it.
 */
static state_handler_fn_t state_handlers[PORT_STATE_MAX] = {
    process_invalid_state,           /* PORT_INVALID_STATE */
    process_uninit_state,            /* PORT_UNINIT */
    process_disabled_state,          /* PORT_DISABLED */
    process_fault_state,             /* PORT_FAULT */
    process_wait_pd_state,           /* PORT_WAIT_PD */
    process_l1_neg_complete_state,   /* PORT_L1_NEG_COMPLETE */
    process_wait_lldp_req_state,     /* PORT_WAIT_LLDP_REQ */
    process_default_pwr_limit_state, /* PORT_DEFAULT_PWR_LIMIT */
    process_l2_neg_complete_state,   /* PORT_L2_NEG_COMPLETE */
    process_lost_power_link_state,   /* PORT_LOST_POWER_LINK */
};

/**
 * process_invalid_state - Invalid state handler
 * @port: port to be processed
 * @data: (ignored)
 */
DECLARE_STATE_HANDLER(PORT_INVALID_STATE, process_invalid_state)
{
    if (!port) {
        POE_DEBUG("Port arg is NULL");
        return PORT_EVENT_IDLE;
    }

    POE_ERR("Port %s is in INVALID_STATE due to an illegal transition "
            "(shouldn't have got here)",
            port->ifname);

    return PORT_EVENT_IDLE;
}

/**
 * determine_l1_port_state - Decide whether the port is in either
 * disabled, enabled or error state
 *
 * @port: port to be processed
 */
static enum port_state_event
determine_l1_port_state(struct port_state_machine *port)
{
    if (!port) {
        POE_DEBUG("Port arg is NULL");
        return PORT_EVENT_IDLE;
    }

    struct poed_payload *query = create_get_port_details_query(port->id);
    if (LLDP_POED_ERR_OK != sync_send_poed_request(query, "get_port_details")) {
        POE_ERR("Failed to send get_port_details request for %s", port->ifname);
        free_payload(query);
        free(query);
        return PORT_EVENT_IDLE;
    }

    /**
     * Parse the payload and generate event.
     * The port may be in error state, hence check the status first.
     */
    const struct poed_payload *is_admin_enabled = NULL;
    const struct poed_payload *status = NULL;
    const struct poed_payload *is_lldp_enabled = NULL;
    enum port_state_event result = PORT_EVENT_IDLE;
    if (0 ==
            find_payload_by_key(query, "is_admin_enabled", &is_admin_enabled) &&
        0 == find_payload_by_key(query, "status", &status) &&
        0 == find_payload_by_key(query, "is_lldp_enabled", &is_lldp_enabled)) {
        if (!(PAYLOAD_VALUE_BOOLEAN == is_admin_enabled->type &&
              PAYLOAD_VALUE_STRING == status->type &&
              PAYLOAD_VALUE_BOOLEAN == is_lldp_enabled->type)) {
            POE_ERR("Invalid payload type");
            goto parsing_failed;
        }

        if (is_admin_enabled->value.val_bool)
            result = PORT_EVENT_PORT_ENABLED;
        else if (!is_admin_enabled->value.val_bool)
            result = PORT_EVENT_PORT_DISABLED;
        else if (0 == strcasecmp(status->value.val_str, "err"))
            result = PORT_EVENT_ERR;

        port->admin_lldp_enabled = is_lldp_enabled->value.val_bool;
    } else
        goto parsing_failed;

    free_payload(query);
    free(query);

    return result;

parsing_failed:
    POE_ERR("Failed to parse the poed payload for %s", port->ifname);
    free_payload(query);
    free(query);
    return PORT_EVENT_IDLE;
}

/**
 * process_uninit_state - Process an uninitialized port
 * @port: port to be processed
 * @data: (ignored)
 *
 * Usually, all ports are initialized by init_ports() and are either disabled or
 * waiting for link. However, a connection reset event will render the port back
 * to uninitialized, thus requiring us to query the poed agent of its state.
 * This relies on sending a synchronous request for detecting the current status
 * of the port and acting on the reported status change in init_ports().
 */
DECLARE_STATE_HANDLER(PORT_UNINIT, process_uninit_state)
{
    enum port_state_event result = determine_l1_port_state(port);

    if (PORT_EVENT_ERR == result)
        POE_CRIT("Port %s went into fault state from uninit state",
                 port->ifname);

    return result;
}

/**
 * process_disabled_state - Process a disabled port
 * @port: port to be processed
 * @data: (ignored)
 *
 * Query the poed agent to detect whether the port was enabled by the user. A
 * port that has a down operational status, doesn't necessarily mean it's
 * enabled or disabled.
 * For instance, a port must have both a power link and a data link to
 * go up to PORT_L2_NEG_COMPLETE.
 */
DECLARE_STATE_HANDLER(PORT_DISABLED, process_disabled_state)
{
    enum port_state_event result = determine_l1_port_state(port);

    if (PORT_EVENT_ERR == result) {
        POE_CRIT("Port %s went into fault state from disabled state",
                 port->ifname);
    } else if (PORT_EVENT_PORT_DISABLED == result)
        return PORT_EVENT_IDLE; /* Port is already disabled. */

    return result;
}

/**
 * process_fault_state - Process a port in error state
 * @port: port to be processed
 * @data: (ignored)
 *
 * Query the poed agent to detect whether the port has recovered as enabled or
 * disabled.
 */
DECLARE_STATE_HANDLER(PORT_FAULT, process_fault_state)
{
    enum port_state_event result = determine_l1_port_state(port);

    if (PORT_EVENT_ERR == result)
        return PORT_EVENT_IDLE; /* Port is already in error state. */

    return result;
}

/**
 * process_wait_pd_state - Process a port which is waiting for the L1
 * negotiation to complete after a PD is connected
 * @port: port to be processed
 * @data: (ignored)
 *
 * A successful transition is considered if, at least, the port has an active
 * operational status and, not necessarily, an active data link (the PD may not
 * support DLL classification at all).
 */
DECLARE_STATE_HANDLER(PORT_WAIT_PD, process_wait_pd_state)
{
    if (!port) {
        POE_DEBUG("Port arg is NULL");
        return PORT_EVENT_IDLE;
    }

    struct poed_payload *query = create_get_port_details_query(port->id);
    if (0 != sync_send_poed_request(query, "get_port_details")) {
        POE_ERR("Failed to send get_port_details request for %s", port->ifname);
        free_payload(query);
        free(query);
        return PORT_EVENT_IDLE;
    }

    /**
     * Parse the payload and generate event.
     * The port may be in error state, hence check the status first.
     * In case the port was working already as an L2 port, go to
     * L2_NEG_COMPLETE.
     */
    const struct poed_payload *is_admin_enabled = NULL;
    const struct poed_payload *status = NULL;
    const struct poed_payload *power_mode = NULL;
    const struct poed_payload *assigned_class = NULL;
    const struct poed_payload *tppl = NULL;
    const struct poed_payload *is_lldp_enabled = NULL;
    enum port_state_event result = PORT_EVENT_IDLE;
    if (0 ==
            find_payload_by_key(query, "is_admin_enabled", &is_admin_enabled) &&
        0 == find_payload_by_key(query, "status", &status) &&
        0 == find_payload_by_key(query, "power_mode", &power_mode) &&
        0 == find_payload_by_key(query, "assigned_class", &assigned_class) &&
        0 == find_payload_by_key(query, "tppl", &tppl) &&
        0 == find_payload_by_key(query, "is_lldp_enabled", &is_lldp_enabled)) {
        if (!(PAYLOAD_VALUE_BOOLEAN == is_admin_enabled->type &&
              PAYLOAD_VALUE_STRING == status->type &&
              (PAYLOAD_VALUE_STRING == power_mode->type ||
               PAYLOAD_VALUE_NULL == power_mode->type) &&
              (PAYLOAD_VALUE_NUMBER == assigned_class->type ||
               PAYLOAD_VALUE_NULL == assigned_class->type) &&
              (PAYLOAD_VALUE_NUMBER == tppl->type ||
               PAYLOAD_VALUE_NULL == tppl->type) &&
              PAYLOAD_VALUE_BOOLEAN == is_lldp_enabled->type)) {
            POE_ERR("Invalid payload type");
            goto parsing_failed;
        }

        if (0 == strcasecmp(status->value.val_str, "err")) {
            result = PORT_EVENT_ERR;
        } else if (0 == strcasecmp(status->value.val_str, "on")) {
            if (PAYLOAD_VALUE_NULL == power_mode->type ||
                PAYLOAD_VALUE_NULL == assigned_class->type ||
                PAYLOAD_VALUE_NULL == tppl->type) {
                POE_ERR("Invalid power fields type");
                goto parsing_failed;
            }
            bool is_already_reconciled =
                is_neighbor_already_reconciled(port->ifname);
            if (0 == strcasecmp(power_mode->value.val_str, "l1") ||
                (0 == strcasecmp(power_mode->value.val_str, "l2") &&
                 !is_already_reconciled)) {
                /**
                 * There's the case when a port is running in l2 mode,
                 * because that's the only way for the user to change the TPPL
                 * (through the L2 API).
                 */
                POE_INFO(
                    "Port %s came online and has an "
                    "active power link. Assigned class: %d, current TPPL: %dW, "
                    "data link status: %s",
                    port->ifname, assigned_class->value.val_int,
                    tppl->value.val_int, port->if_up ? "up" : "down");
                result = PORT_EVENT_OK;
            } else if (0 == strcasecmp(power_mode->value.val_str, "l2") &&
                       is_already_reconciled) {
                /**
                 * Port is already working in L2 mode, restore L2_NEG_COMPLETE.
                 */
                result = PORT_EVENT_LLDP_RESTORE;
            }
        } else if (false == is_admin_enabled->value.val_bool)
            result = PORT_EVENT_PORT_DISABLED;

        port->admin_lldp_enabled = is_lldp_enabled->value.val_bool;
    } else
        goto parsing_failed;

    free_payload(query);
    free(query);

    if (PORT_EVENT_ERR == result) {
        POE_CRIT("Port %s went into fault state from wait_pd state",
                 port->ifname);
    } else if (PORT_EVENT_PORT_DISABLED == result) {
        POE_NOTICE("Port %s got disabled in wait_pd state", port->ifname);
    } else if (PORT_EVENT_LLDP_RESTORE == result) {
        POE_NOTICE("Port %s got restored to L2 complete from wait_pd state",
                   port->ifname);
    } else if (PORT_EVENT_OK == result) {
        POE_NOTICE("Port %s completed the L1 negotiation successfully",
                   port->ifname);
    }

    return result;

parsing_failed:
    POE_ERR("Failed to parse the poed payload for %s", port->ifname);
    free_payload(query);
    free(query);
    return PORT_EVENT_IDLE;
}

/**
 * create_set_power_limit_query - Populate the poed_payload fields for the
 * set_power_limit method
 * @id: port ID
 * @set_default: default power limit flag
 * @requested_power: PD requested power (single-signature, nullable)
 * @priority: 802.3at power priority (nullable)
 * @requested_power_a: PD requested power for mode A (dual-signature, nullable)
 * @requested_power_b: PD requested power for mode B (dual-signature, nullable)
 *
 * @warning: caller has the responsibility to free the returned payload memory.
 *
 * Returns the newly created payload.
 */
static struct poed_payload *create_set_power_limit_query(
    port_id_t id, bool set_default, unsigned requested_power, unsigned priority,
    unsigned requested_power_a, unsigned requested_power_b)
{
    struct poed_payload *params = malloc(4 * sizeof(struct poed_payload));
    struct poed_payload *set_query = malloc(sizeof(struct poed_payload));
    if (!params || !set_query)
        return NULL;

    strncpy(params[0].name, "port_id", PAYLOAD_NAME_MAX_SIZE);
    params[0].type = PAYLOAD_VALUE_NUMBER;
    params[0].value.val_int = id;
    strncpy(params[1].name, "default_power", PAYLOAD_NAME_MAX_SIZE);
    params[1].type = PAYLOAD_VALUE_BOOLEAN;
    params[1].value.val_bool = set_default;

    strncpy(params[2].name, "dot3at", PAYLOAD_NAME_MAX_SIZE);
    if (0 != requested_power) {
        struct poed_payload *dot3at = malloc(2 * sizeof(struct poed_payload));
        strncpy(dot3at[0].name, "requested_power", PAYLOAD_NAME_MAX_SIZE);
        dot3at[0].type = PAYLOAD_VALUE_NUMBER;
        dot3at[0].value.val_int = requested_power;
        strncpy(dot3at[1].name, "priority", PAYLOAD_NAME_MAX_SIZE);
        dot3at[1].type = PAYLOAD_VALUE_NUMBER;
        dot3at[1].value.val_int = priority;

        params[2].type = PAYLOAD_VALUE_OBJECT;
        params[2].child_count = 2;
        params[2].children = dot3at;
    } else {
        params[2].type = PAYLOAD_VALUE_NULL;
        params[2].child_count = 0;
        params[2].children = NULL;
    }
    strncpy(params[3].name, "dot3bt", PAYLOAD_NAME_MAX_SIZE);
    if (requested_power_a || requested_power_b) {
        struct poed_payload *dot3bt = malloc(2 * sizeof(struct poed_payload));
        strncpy(dot3bt[0].name, "mode_a_requested_power",
                PAYLOAD_NAME_MAX_SIZE);
        dot3bt[0].type = PAYLOAD_VALUE_NUMBER;
        dot3bt[0].value.val_int = requested_power_a;
        strncpy(dot3bt[0].name, "mode_b_requested_power",
                PAYLOAD_NAME_MAX_SIZE);
        dot3bt[1].type = PAYLOAD_VALUE_NUMBER;
        dot3bt[1].value.val_int = requested_power_b;
        strncpy(dot3bt[1].name, "mode_b_requested_power",
                PAYLOAD_NAME_MAX_SIZE);

        params[3].type = PAYLOAD_VALUE_OBJECT;
        params[3].child_count = 2;
        params[3].children = dot3bt;
    } else {
        params[3].type = PAYLOAD_VALUE_NULL;
        params[3].child_count = 0;
        params[3].children = NULL;
    }

    strncpy(set_query->name, "params", PAYLOAD_NAME_MAX_SIZE);
    set_query->type = PAYLOAD_VALUE_OBJECT;
    set_query->child_count = 4;
    set_query->children = params;

    return set_query;
}

/**
 * send_set_default_power_limit_request - Send a request for setting the default
 * power limit for the given port
 * @id: port ID
 *
 * Returns 0, LLDP_POED_ERR_OK, if successful, an lldp_poed_err otherwise.
 */
static int send_set_default_power_limit_request(port_id_t id)
{
    struct poed_payload *query =
        create_set_power_limit_query(id, true, 0, 0, 0, 0);
    if (0 != sync_send_poed_request(query, "set_power_limit")) {
        POE_ERR("Failed to send set_power_limit request for port ID %d", id);
        free_payload(query);
        free(query);
        return LLDP_POED_ERR_SEND_REQUEST_FAILED;
    }

    const struct poed_payload *result = NULL;
    int status = LLDP_POED_ERR_OK;
    if (0 == find_payload_by_key(query, "result", &result)) {
        if (PAYLOAD_VALUE_NUMBER != result->type)
            status = LLDP_POED_ERR_INVALID_PAYLOAD;
        else
            POE_DEBUG("Port ID %d, TPPL (W, at PSE output): %d", id,
                      result->value.val_int);
    } else {
        POE_ERR("Failed to parse the poed payload for port ID %d", id);
        status = LLDP_POED_ERR_INVALID_PAYLOAD;
    }
    free_payload(query);
    free(query);

    return status;
}

/**
 * fill_at_power_settings - Populate the dot3 power settings with the parsed
 * payload
 * @at_payload: payload to parse
 * @config: output dot3 config
 *
 * Returns 0 if successful, 1 otherwise.
 */
static int fill_at_power_settings(const struct poed_payload *at_payload,
                                  struct port_dot3_power_settings *config)
{
    if (!at_payload || !config)
        return 1;

    const struct poed_payload *pse_type = NULL;
    const struct poed_payload *priority = NULL;
    const struct poed_payload *requested_power = NULL;
    const struct poed_payload *allocated_power = NULL;
    if (0 == find_payload_by_key(at_payload, "pse_type", &pse_type) &&
        0 == find_payload_by_key(at_payload, "priority", &priority) &&
        0 == find_payload_by_key(at_payload, "requested_power",
                                 &requested_power) &&
        0 == find_payload_by_key(at_payload, "allocated_power",
                                 &allocated_power)) {
        if (PAYLOAD_VALUE_NULL == pse_type->type ||
            PAYLOAD_VALUE_NULL == allocated_power->type ||
            PAYLOAD_VALUE_NULL == requested_power->type) {
            POE_ERR("Invalid 802.3at payload type");
            goto parsing_failed;
        }

        if (0 == strcasecmp(pse_type->value.val_str, "type_2")) {
            POE_DEBUG("Type 2 PSE");
            config->power_type = LLDP_DOT3_POWER_8023AT_TYPE2;
        } else if (0 == strcasecmp(pse_type->value.val_str, "type_3")) {
            POE_DEBUG("Type 3 PSE");
            config->power_type = LLDP_DOT3_POWER_8023AT_TYPE2;
            config->power_type_ext = LLDP_DOT3_POWER_8023BT_TYPE3;
        } else {
            POE_ERR("Unsupported PSE type");
            goto parsing_failed;
        }
        /**
         * May need to factor for backup sources too in the future.
         */
        config->power_source = LLDP_DOT3_POWER_SOURCE_PRIMARY;
        config->power_priority = ((PAYLOAD_VALUE_NULL != priority->type)
                                      ? priority->value.val_int
                                      : LLDP_DOT3_POWER_PRIO_UNKNOWN);
        config->pd_requested = requested_power->value.val_int;
        config->pse_allocated = allocated_power->value.val_int;
    } else
        goto parsing_failed;

    return 0;

parsing_failed:
    POE_ERR("Failed to parse the 802.3at payload");
    return 1;
}

/**
 * fill_bt_power_settings - Populate the dot3 power settings with the parsed
 * payload
 * @at_payload: payload to parse
 * @config: output dot3 config
 *
 * Returns 0, LLDP_POED_ERR_OK, if successful, an lldp_poed_err otherwise.
 */
static int fill_bt_power_settings(const struct poed_payload *bt_payload,
                                  struct port_dot3_power_settings *config)
{
    if (!bt_payload || !config)
        return LLDP_POED_ERR_INVALID_PARAM;

    int func_status = LLDP_POED_ERR_OK;
    /**
     * TODO: Dual-signature PD handling.
     */
    const struct poed_payload *pse_power_status = NULL;
    const struct poed_payload *pse_power_pairs = NULL;
    const struct poed_payload *max_power = NULL;
    if (0 == find_payload_by_key(bt_payload, "pse_power_status",
                                 &pse_power_status) &&
        0 == find_payload_by_key(bt_payload, "pse_power_pairs",
                                 &pse_power_pairs) &&
        0 == find_payload_by_key(bt_payload, "max_power", &max_power)) {
        if (PAYLOAD_VALUE_NUMBER != pse_power_status->type ||
            PAYLOAD_VALUE_NUMBER != pse_power_pairs->type ||
            PAYLOAD_VALUE_NUMBER != max_power->type) {
            POE_ERR("Invalid 802.3bt payload type");
            func_status = LLDP_POED_ERR_INVALID_8023BT_FIELDS;
            goto parsing_failed;
        }

        config->pse_power_pair =
            ((0 == strcasecmp(pse_power_pairs->value.val_str, "mode_b"))
                 ? LLDP_DOT3_POWERPAIRS_SPARE
                 : LLDP_DOT3_POWERPAIRS_SIGNAL);
        config->pd_4pid = 0;
        config->pd_requested_a = USHRT_MAX;
        config->pd_requested_b = USHRT_MAX;
        config->pse_allocated_a = USHRT_MAX;
        config->pse_allocated_b = USHRT_MAX;
        config->pse_status = pse_power_status->value.val_int;
        config->pd_status = 0;
        config->pse_pairs_ext =
            (0 == strcasecmp(pse_power_status->value.val_str, "mode_b"))
                ? 0x2
                : ((0 == strcasecmp(pse_power_status->value.val_str, "mode_a"))
                       ? 0x1
                       : 0x3); /* Both modes, otherwise set to signal or spare
                                */
        config->power_class_mode_a = -1;
        config->power_class_mode_b = -1;
        /**
         * Power Class ext was already initialized with the assigned class.
         * However, for a dual-signature PD, this field must be set to 0xF.
         */
        if (0x3 == config->pse_status)
            config->pd_power_class_ext = 0xF;
        /* Power Type ext already set in fill_at_power_settings() for a 802.3bt
         * PSE. */
        config->pd_load = 0;
        config->pse_max_available_power = max_power->value.val_int;
    } else {
        func_status = LLDP_POED_ERR_PARSE_ERROR;
        goto parsing_failed;
    }

    return func_status;

parsing_failed:
    POE_ERR("Failed to parse the 802.3bt payload");
    return func_status;
}

/**
 * send_lldp_neg_confirmation - Advertise the current PSE configuration to the
 * LLDP neighbor
 * @port: port to be processed
 * @event: in case a transition to an error state is necessary (nullable)
 * @is_initial: initial MDI advertisement flag
 *
 * In order to send a power advertisement, the port must be on and
 * have an active data link. Otherwise, this will fail.
 *
 * Returns 0, LLDP_POED_ERR_OK, if successful, an lldp-poed_err otherwise.
 */
static int advertise_pse_dot3_config(struct port_state_machine *port,
                                     enum port_state_event *event,
                                     bool is_initial)
{
    struct poed_payload *query = create_get_port_details_query(port->id);
    if (0 != sync_send_poed_request(query, "get_port_details")) {
        POE_ERR("Failed to send get_port_details request for %s", port->ifname);
        free_payload(query);
        free(query);
        return LLDP_POED_ERR_GETPORTDETAILS_FAILED;
    }

    /**
     * Parse the payload and generate event. If there was any problem with the
     * power link, then this will go to CONN_RESET. In the happy case, we rely
     * on the LLDP MDI power advertisement to be sent successfully.
     */
    const struct poed_payload *is_admin_enabled = NULL;
    const struct poed_payload *status = NULL;
    const struct poed_payload *power_mode = NULL;
    const struct poed_payload *assigned_class = NULL;
    const struct poed_payload *is_lldp_enabled = NULL;
    const struct poed_payload *dot3at = NULL;
    const struct poed_payload *dot3bt = NULL;
    int func_status = LLDP_POED_ERR_OK;
    if (0 ==
            find_payload_by_key(query, "is_admin_enabled", &is_admin_enabled) &&
        0 == find_payload_by_key(query, "status", &status) &&
        0 == find_payload_by_key(query, "power_mode", &power_mode) &&
        0 == find_payload_by_key(query, "assigned_class", &assigned_class) &&
        0 == find_payload_by_key(query, "is_lldp_enabled", &is_lldp_enabled) &&
        0 == find_payload_by_key(query, "dot3at", &dot3at) &&
        0 == find_payload_by_key(query, "dot3bt", &dot3bt)) {
        if (!(PAYLOAD_VALUE_BOOLEAN == is_admin_enabled->type &&
              PAYLOAD_VALUE_STRING == status->type &&
              (PAYLOAD_VALUE_NULL == power_mode->type ||
               PAYLOAD_VALUE_STRING == power_mode->type) &&
              (PAYLOAD_VALUE_NULL == assigned_class->type ||
               PAYLOAD_VALUE_NUMBER == assigned_class->type) &&
              PAYLOAD_VALUE_BOOLEAN == is_lldp_enabled->type &&
              (PAYLOAD_VALUE_NULL == dot3at->type ||
               PAYLOAD_VALUE_OBJECT == dot3at->type) &&
              (PAYLOAD_VALUE_NULL == dot3bt->type ||
               PAYLOAD_VALUE_OBJECT == dot3bt->type))) {
            POE_ERR("Invalid payload type");
            func_status = LLDP_POED_ERR_INVALID_PAYLOAD;
            goto parsing_failed;
        }
        if (0 == strcasecmp(status->value.val_str, "on")) {
            /**
             * If either LLDP processing is disabled or
             * the data link is not active, then we'll return an error.
             * In this case, the PD can still come back online later and send
             * an L2 power request to be reconciled.
             */
            if (!is_lldp_enabled->value.val_bool) {
                POE_WARN("LLDP processing is disabled for port %s. Will skip "
                         "advertising",
                         port->ifname);
                func_status = LLDP_POED_ERR_LLDP_PROCESSING_DISABLED;
                *event = PORT_EVENT_ERR;
            } else if (!port->if_up) {
                POE_WARN("Port %s does not have an "
                         "active data link. Will skip advertising",
                         port->ifname);
                func_status = LLDP_POED_ERR_INACTIVE_DATALINK;
                *event = PORT_EVENT_ERR;
            } else if ((0 == strcasecmp(power_mode->value.val_str, "l1") &&
                        is_initial) ||
                       (0 == strcasecmp(power_mode->value.val_str, "l2") &&
                        !is_neighbor_already_reconciled(port->ifname)) ||
                       (0 == strcasecmp(power_mode->value.val_str, "l2") &&
                        !is_initial)) { /* If the port is already in L2 mode,
                                           then this can't be the initial
                                           advertisement. */
                if (PAYLOAD_VALUE_NULL == dot3at->type) {
                    POE_ERR("802.3at fields are mandatory");
                    func_status = LLDP_POED_ERR_8023AT_FIELDS_MISSING;
                    goto parsing_failed;
                }
                /**
                 * We're good to send the MDI advertisement.
                 * Fill in the basic 802.1ab fields first.
                 */
                struct port_dot3_power_settings pse_config = {
                    .poe_device_type = LLDP_DOT3_POWER_PSE,
                    .mdi_supported = 1,
                    .mdi_enabled = 1,
                    .mdi_paircontrol = 1,
                    .pse_power_pair = LLDP_DOT3_POWERPAIRS_SIGNAL,
                    /* TODO: dual-signature PDs handling for Power Class. */
                    .pd_class = ((assigned_class->value.val_int >= 4)
                                     ? 5
                                     : assigned_class->value.val_int + 1),
                    /* Fill this in for 802.3bt ease of processing. */
                    .pd_power_class_ext = assigned_class->value.val_int,
                    /* This is going to be enabled if dot3bt payload is present.
                     */
                    .power_type_ext = LLDP_DOT3_POWER_8023BT_OFF,
                };

                if (0 != fill_at_power_settings(dot3at, &pse_config)) {
                    POE_ERR("Failed to fill in the 802.3at fields for %s.",
                            port->ifname);
                    func_status = LLDP_POED_ERR_INVALID_8023AT_FIELDS;
                } else if (PAYLOAD_VALUE_OBJECT == dot3bt->type &&
                           0 != (func_status = fill_bt_power_settings(
                                     dot3bt, &pse_config))) {
                    POE_ERR("Failed to fill in the 802.3bt fields for %s.",
                            port->ifname);
                } else if (0 != (func_status = send_mdi_pse_advertisement(
                                     port->ifname, &pse_config,
                                     &port->timeout_time))) {
                    POE_ERR("Failed to send the MDI power advertisement for %s",
                            port->ifname);
                    port->timeout_time = 0;
                } else {
                    /* Success. */
                    POE_INFO(
                        "Successfully sent the MDI power advertisement for %s ",
                        port->ifname);
                }
            }
        } else if (false == is_admin_enabled->value.val_bool ||
                   0 == strcasecmp(status->value.val_str, "off") ||
                   0 == strcasecmp(status->value.val_str, "err")) {
            /* Port got disabled in the meantime or lost the PD connection. */
            *event = PORT_EVENT_LOST_POWER;
        }

        port->admin_lldp_enabled = is_lldp_enabled;
    } else
        goto parsing_failed;

    free_payload(query);
    free(query);

    return func_status;

parsing_failed:
    POE_ERR("Failed to parse the poed payload for %s", port->ifname);
    free_payload(query);
    free(query);
    return func_status;
}

/**
 * process_l1_neg_complete_state - Process a port which finished the L1
 * negotiation
 * @port: port to be processed
 * @data: (ignored)
 *
 * Send the initial MDI power advertisement for the port which completed the L1
 * negotiation and that was classified by the PoE chipset, only if there is an
 * active data and power link state and LLDP processing is enabled. If the
 * advertisement was sent successfully, then this will transition to the next
 * state (WAIT_LLDP_REQ). If sending the MDI advertisement fails for any reason,
 * we fall back to the default power limit.
 */
DECLARE_STATE_HANDLER(PORT_L1_NEG_COMPLETE, process_l1_neg_complete_state)
{
    if (!port) {
        POE_DEBUG("Port arg is NULL");
        return PORT_EVENT_IDLE;
    }

    enum port_state_event result = PORT_EVENT_IDLE;
    if (0 == advertise_pse_dot3_config(port, &result, true)) {
        result = PORT_EVENT_OK;
    } else if (PORT_EVENT_LOST_POWER != result) {
        POE_WARN(
            "Failed to send the initial MDI "
            "power advertisement. Trying to set the default power limit for %s",
            port->ifname);
        if (0 != send_set_default_power_limit_request(port->id)) {
            POE_ERR(
                "Failed to set the default power limit for port %s. Will retry",
                port->ifname);
            result = PORT_EVENT_IDLE;
        } else {
            /**
             * The statement is a bit misleading, as the default power limit
             * was assigned successfully, but the dot3 advertisement failed.
             * This means we are going to PORT_DEFAULT_PWR_LIMIT.
             */
            result = PORT_EVENT_ERR;
            port->lldp_default_pwr_limit_update_pending = true;
        }
    }

    if (PORT_EVENT_LOST_POWER == result) {
        POE_NOTICE("Port %s lost the PD power link in l1_neg_complete state",
                   port->ifname);
    }

    return result;
}

/**
 * send_set_l2_power_limit_request - Convert the power settings to a poed
 * message for setting the new power limit
 * @id: port ID
 * @settings: the dot3 power settings to be used for the command
 *
 * Returns 0, LLDP_POED_ERR_OK, if successful, an lldp_poed_err otherwise.
 */
static int
send_set_l2_power_limit_request(port_id_t id,
                                const struct port_dot3_power_settings *settings)
{
    if (!settings)
        return LLDP_POED_ERR_INVALID_PARAM;

    /**
     * TODO: dual-signature reconciliation.
     */
    struct poed_payload *query = create_set_power_limit_query(
        id, false, settings->pd_requested, settings->power_priority, 0, 0);
    if (0 != sync_send_poed_request(query, "set_power_limit")) {
        POE_ERR("Failed to send set_power_limit request for port ID %d", id);
        free_payload(query);
        free(query);
        return LLDP_POED_ERR_SEND_REQUEST_FAILED;
    }

    const struct poed_payload *result = NULL;
    int status = LLDP_POED_ERR_OK;
    if (0 == find_payload_by_key(query, "result", &result)) {
        if (PAYLOAD_VALUE_NUMBER != result->type || !result->value.val_int)
            status = LLDP_POED_ERR_INVALID_PAYLOAD;
    } else {
        POE_ERR("Failed to parse the poed payload for port ID %d", id);
        status = LLDP_POED_ERR_PARSE_ERROR;
    }
    free_payload(query);
    free(query);

    return status;
}

/**
 * reconcile_pd_power_request - Compare the dot3 configuration with the current
 * PSE config and apply the LLDP PD power request, if possible
 * @config: PD dot3 power config to process
 * @event: in case a transition to an error state is necessary
 * @port: port to process
 *
 * Returns 0, LLDP_POED_ERR_OK, if successful, an lldp_poed_err otherwise.
 */
static int
reconcile_pd_power_request(const struct port_dot3_power_settings *config,
                           enum port_state_event *event,
                           struct port_state_machine *port)
{
    if (!config || !event || !port)
        return LLDP_POED_ERR_INVALID_PARAM;

    /**
     * Cannot rely on the initial port details that were advertised to the PD.
     * Hence, fetch again the PSE config.
     */
    struct poed_payload *query = create_get_port_details_query(port->id);
    if (0 != sync_send_poed_request(query, "get_port_details")) {
        POE_ERR("Failed to send "
                "get_port_details request for %s",
                port->ifname);
        free_payload(query);
        free(query);
        return LLDP_POED_ERR_GETPORTDETAILS_FAILED;
    }

    const struct poed_payload *is_admin_enabled = NULL;
    const struct poed_payload *status = NULL;
    const struct poed_payload *power_mode = NULL;
    const struct poed_payload *assigned_class = NULL;
    const struct poed_payload *is_lldp_enabled = NULL;
    const struct poed_payload *dot3at = NULL;
    const struct poed_payload *dot3bt = NULL;
    int func_status = LLDP_POED_ERR_OK;
    if (0 == find_payload_by_key(query, "is_admin_enabled", &is_admin_enabled) &&
        0 == find_payload_by_key(query, "status", &status) &&
        0 == find_payload_by_key(query, "power_mode", &power_mode) &&
        0 == find_payload_by_key(query, "assigned_class", &assigned_class) &&
        0 == find_payload_by_key(query, "is_lldp_enabled", &is_lldp_enabled) &&
        0 == find_payload_by_key(query, "dot3at", &dot3at) &&
        0 == find_payload_by_key(query, "dot3bt", &dot3bt)) {
        if (!(PAYLOAD_VALUE_BOOLEAN == is_admin_enabled->type &&
              PAYLOAD_VALUE_STRING == status->type &&
              (PAYLOAD_VALUE_NULL == power_mode->type ||
               PAYLOAD_VALUE_STRING == power_mode->type) &&
              (PAYLOAD_VALUE_NULL == assigned_class->type ||
               PAYLOAD_VALUE_NUMBER == assigned_class->type) &&
              PAYLOAD_VALUE_BOOLEAN == is_lldp_enabled->type &&
              (PAYLOAD_VALUE_NULL == dot3at->type ||
               PAYLOAD_VALUE_OBJECT == dot3at->type) &&
              (PAYLOAD_VALUE_NULL == dot3bt->type ||
               PAYLOAD_VALUE_OBJECT == dot3bt->type))) {
            POE_ERR("Invalid payload type");
            func_status = LLDP_POED_ERR_INVALID_PAYLOAD;
            goto parsing_failed;
        }
        if (false == is_admin_enabled->value.val_bool ||
            0 == strcasecmp(status->value.val_str, "off") ||
            0 == strcasecmp(status->value.val_str, "err")) {
            /* Port got disabled in the meantime or lost the PD connection. */
            *event = PORT_EVENT_LOST_POWER;
            free_payload(query);
            free(query);
            return LLDP_POED_ERR_PORT_GOT_DISABLED;
        }

        if (PAYLOAD_VALUE_NULL == dot3at->type) {
            POE_ERR("802.3at fields are mandatory");
            func_status = LLDP_POED_ERR_8023AT_FIELDS_MISSING;
            goto parsing_failed;
        }
        if (0 == strcasecmp(status->value.val_str, "on")) {
            if (!is_lldp_enabled->value.val_bool) {
                POE_WARN("LLDP processing is "
                         "disabled for port %s",
                         port->ifname);
                func_status = LLDP_POED_ERR_LLDP_PROCESSING_DISABLED;
            } else if (!port->if_up) {
                POE_WARN("Port %s does not have an "
                         "active data link",
                         port->ifname);
                func_status = LLDP_POED_ERR_INACTIVE_DATALINK;
            } else {
                /**
                 * Parse the local PSE config from the query.
                 * Note that the 802.1ab fields are left out intentionally.
                 */
                struct port_dot3_power_settings pse_config = {
                    .power_type_ext = LLDP_DOT3_POWER_8023BT_OFF,
                };
                if (0 != fill_at_power_settings(dot3at, &pse_config)) {
                    POE_ERR("Failed to parse the 802.3at fields for %s",
                            port->ifname);
                    func_status = LLDP_POED_ERR_INVALID_8023AT_FIELDS;
                } else if (PAYLOAD_VALUE_OBJECT == dot3bt->type &&
                           0 != (func_status = fill_bt_power_settings(
                                     dot3bt, &pse_config))) {
                    POE_ERR("Failed to parse the the 802.3bt fields for %s",
                            port->ifname);
                    func_status = LLDP_POED_ERR_INVALID_8023BT_FIELDS;
                }

                if (LLDP_DOT3_POWER_PSE == config->poe_device_type) {
                    /* Somebody plugged in a PSE instead of a PD... */
                    POE_WARN("Unexpected PD PoE device type for port %s",
                             port->ifname);
                    func_status = LLDP_POED_ERR_UNEXPECTED_DEVICE_TYPE;
                } else if (0x0 == config->pd_load ||
                           LLDP_DOT3_POWER_8023BT_OFF ==
                               config->power_type_ext) {
                    /**
                     * If we don't support, as a PSE, the 802.3bt standard, just
                     * hope for the best... Alternatively, rely on the PSE
                     * maximum available power field.
                     */
                    if (LLDP_DOT3_POWER_8023BT_OFF ==
                            pse_config.power_type_ext ||
                        config->pd_requested <=
                            pse_config.pse_max_available_power) {
                        func_status =
                            send_set_l2_power_limit_request(port->id, config);
                    } else {
                        POE_ERR("Failed to set the L2 TPPL for %s",
                                port->ifname);
                        func_status = LLDP_POED_ERR_FAILED_TO_SET_L2_TPPL;
                    }
                } else if (0x1 == config->pd_load) {
                    /**
                     * TODO: Reconcile dual-signature
                     */
                    POE_ERR("Dual-signature PDs are not supported");
                    func_status = LLDP_POED_ERR_DUALSIG_PD_NOT_SUPPORTED;
                }
            }
        }
        port->admin_lldp_enabled = is_lldp_enabled;
    } else
        goto parsing_failed;

    free_payload(query);
    free(query);

    return func_status;

parsing_failed:
    POE_ERR("Failed to parse the poed "
            "payload for %s",
            port->ifname);
    free_payload(query);
    free(query);
    return func_status;
}

/**
 * is_port_on - Check if the port is operationally active
 * @id: port ID
 *
 * If the request or parsing fails, this will return false.
 *
 * Returns true, if the port is on, false otherwise.
 */
static bool is_port_on(port_id_t id)
{
    struct poed_payload *query = create_get_port_details_query(id);
    if (LLDP_POED_ERR_OK != sync_send_poed_request(query, "get_port_details")) {
        POE_ERR("Failed to send get_port_details request for port ID %d", id);
        free_payload(query);
        free(query);
        return false;
    }

    bool result = false;
    const struct poed_payload *status = NULL;
    if (0 == find_payload_by_key(query, "status", &status)) {
        if (PAYLOAD_VALUE_STRING != status->type) {
            POE_ERR("Invalid payload type");
            goto parsing_failed;
        }

        if (0 == strcasecmp("on", status->value.val_str))
            result = true;
    } else
        goto parsing_failed;

    free_payload(query);
    free(query);

    return result;

parsing_failed:
    POE_ERR("Failed to parse the poed payload for port ID %d", id);
    free_payload(query);
    free(query);
    return result;
}

/**
 * process_wait_lldp_req_state - Process a port waiting for an LLDP power
 * request
 * @port: port to be processed
 * @data: LLDP neighbor update, containing the power request (nullable)
 *
 * If the handler receives a non-NULL update, then this update is parsed and the
 * request is reconciled against the current port status. If the update is NULL,
 * then this will compare the current time with the timeout value and fall back
 * to the default power limit, if the PD timed out.
 */
DECLARE_STATE_HANDLER(PORT_WAIT_LLDP_REQ, process_wait_lldp_req_state)
{
    if (!port) {
        POE_ERR("Port arg is NULL");
        return PORT_EVENT_IDLE;
    }

    /**
     * Need to check first on the port status, otherwise we run
     * the risk of stalling in the current state.
     */
    enum port_state_event result = PORT_EVENT_IDLE;
    bool port_on = false;
    if (!(port_on = is_port_on(port->id)))
        result = PORT_EVENT_LOST_POWER;

    int func_status = LLDP_POED_ERR_OK;
    const struct port_neighbor_update *update = data;
    if (port_on && update) {
        if (update->was_deleted) {
            /**
             * We lost the LLDP neighbor here. We will most likely
             * transitions to default power state when the timeout expires.
             */
            POE_WARN("Unexpected deleted neighbor");
            func_status = LLDP_POED_ERR_UNEXPECTED_DELETED_NEIGHBOR;
        } else {
            if (0 == (func_status = reconcile_pd_power_request(
                          &update->settings, &result, port))) {
                POE_INFO("PD power request reconciled successfully for %s",
                         port->ifname);
                if (0 == (func_status = advertise_pse_dot3_config(port, &result,
                                                                  false))) {
                    POE_INFO("Advertised the new "
                             "power configuration successfully for %s",
                             port->ifname);
                    result = PORT_EVENT_OK;
                } else if (PORT_EVENT_LOST_POWER != result) {
                    POE_ERR("Failed to advertise "
                            "the new power configuration for %s",
                            port->ifname);
                    result = PORT_EVENT_IDLE;
                }
            } else {
                POE_WARN("Failed to reconcile the power request for %s",
                         port->ifname);
            }
        }
    } else if (port_on) {
        /**
         * If there is no update, check if the timeout hasn't expired yet.
         */
        time_t current_time = time(NULL);
        if (current_time > port->timeout_time) {
            if (LLDP_POED_ERR_OK !=
                (func_status =
                     send_set_default_power_limit_request(port->id))) {
                POE_ERR("Failed to set the default power limit for port %s",
                        port->ifname);
            } else {
                result = PORT_EVENT_LLDP_TIMEOUT;
                port->timeout_time = 0;
                port->lldp_default_pwr_limit_update_pending = true;
            }
        }
    }

    if (PORT_EVENT_LOST_POWER == result)
        POE_NOTICE("Port %s lost the PD power link in wait_lldp_req state",
                   port->ifname);

    return result;
}

/**
 * process_default_pwr_limit_state - Process a port that failed the L2
 * negotiation or timed out
 * @port: port to be processed
 * @data: LLDP neighbor update, containing the power request (nullable)
 *
 * If the LLDP neighbor will send a valid PD power request, we'll still try to
 * reconcile it.
 */
DECLARE_STATE_HANDLER(PORT_DEFAULT_PWR_LIMIT, process_default_pwr_limit_state)
{
    if (!port) {
        POE_DEBUG("Port arg is NULL");
        return PORT_EVENT_IDLE;
    }

    /**
     * Need to check first on the port status, otherwise we run
     * the risk of stalling in the current state.
     */
    enum port_state_event result = PORT_EVENT_IDLE;
    bool port_on = false;
    if (!(port_on = is_port_on(port->id))) {
        port->lldp_default_pwr_limit_update_pending = false;
        return PORT_EVENT_LOST_POWER;
    }

    /**
     * A port may be flapped without the state machine detecting the transition
     * and lose the TPPL. Hence, ensuring that the default power limit
     * is still present.
     */
    if (0 != send_set_default_power_limit_request(port->id)) {
        POE_ERR("Failed to set the default power limit for port %s. Will retry",
                port->ifname);
    } else if (port->lldp_default_pwr_limit_update_pending) {
        enum port_state_event lldp_result = PORT_EVENT_IDLE;
        if (LLDP_POED_ERR_OK ==
            advertise_pse_dot3_config(port, &lldp_result, false)) {
            POE_INFO("Advertised the default power configuration "
                     "successfully for %s",
                     port->ifname);
            port->lldp_default_pwr_limit_update_pending = false;
        } else if (PORT_EVENT_LOST_POWER != lldp_result) {
            POE_WARN("Failed to advertise the new power configuration "
                     "for %s",
                     port->ifname);
        } else {
            port->lldp_default_pwr_limit_update_pending = false;
            return PORT_EVENT_LOST_POWER;
        }
    }

    int func_status = LLDP_POED_ERR_OK;
    const struct port_neighbor_update *update = data;
    if (update) {
        if (update->was_deleted) {
            /**
             * We lost the LLDP neighbor here. If it doesn't
             * come back, the port will remain in the L1 default power
             * state forever.
             */
            POE_DEBUG("Unexpected deleted neighbor");
        } else {
            if (LLDP_POED_ERR_OK == (func_status = reconcile_pd_power_request(
                                         &update->settings, &result, port))) {
                POE_INFO("PD power request reconciled successfully for %s",
                         port->ifname);
                if (LLDP_POED_ERR_OK ==
                    advertise_pse_dot3_config(port, &result, false)) {
                    POE_INFO("Advertised the new power configuration "
                             "successfully for %s",
                             port->ifname);
                    result = PORT_EVENT_OK;
                    port->lldp_default_pwr_limit_update_pending = false;
                } else if (PORT_EVENT_LOST_POWER != result) {
                    POE_WARN("Failed to advertise the new power configuration "
                             "for %s",
                             port->ifname);
                    result = PORT_EVENT_IDLE;
                }
            } else {
                POE_WARN("Failed to reconcile the power request for %s",
                         port->ifname);
            }
        }
    }

    if (PORT_EVENT_LOST_POWER == result) {
        POE_NOTICE("Port %s lost the PD power link in default_pwr_limit state",
                   port->ifname);
        port->lldp_default_pwr_limit_update_pending = false;
    }

    return result;
}

/**
 * process_l2_neg_complete_state - Process a port that has finished the L2
 * negotiation successfully
 * @port: port to be processed
 * @data: LLDP neighbor update for deleted neighbors
 */
DECLARE_STATE_HANDLER(PORT_L2_NEG_COMPLETE, process_l2_neg_complete_state)
{
    if (!port) {
        POE_DEBUG("port arg is NULL");
        return PORT_EVENT_IDLE;
    }

    struct poed_payload *query = create_get_port_details_query(port->id);
    if (0 != sync_send_poed_request(query, "get_port_details")) {
        POE_ERR("Failed to send get_port_details request for %s", port->ifname);
        free_payload(query);
        free(query);
        return PORT_EVENT_IDLE;
    }

    /**
     * Just check if the port is still providing power.
     * If the port is still active, must check if the LLDP neighbor has not aged
     * out.
     */
    const struct poed_payload *status = NULL;
    const struct port_neighbor_update *update = data;
    enum port_state_event result = PORT_EVENT_IDLE;
    if (0 == find_payload_by_key(query, "status", &status)) {
        if (PAYLOAD_VALUE_STRING != status->type) {
            POE_ERR("Invalid payload type");
            goto parsing_failed;
        }

        if (0 == strcasecmp(status->value.val_str, "off"))
            result = PORT_EVENT_LOST_POWER;
        else if (update && update->was_deleted) {
            /**
             * TODO: Disabling L2 mode is impossible for some firmware versions.
             * At the moment, the port will remain in L2 mode, even though the
             * LLDP neighbor aged out.
             */
            POE_WARN("Port %s neighbor aged out and got deleted", port->ifname);
            result = PORT_EVENT_ERR;
        }
    } else
        goto parsing_failed;

    free_payload(query);
    free(query);

    if (PORT_EVENT_LOST_POWER == result) {
        POE_NOTICE("Port %s lost the PD power link in l2_neg_complete state",
                   port->ifname);
    }

    return result;

parsing_failed:
    POE_ERR("Failed to parse the poed payload for %s", port->ifname);
    free_payload(query);
    free(query);
    return PORT_EVENT_IDLE;
}

/**
 * process_lost_power_link_state - Process a port which lost the power link
 * @port: port to be processed
 * @data: caller custom data
 */
DECLARE_STATE_HANDLER(PORT_LOST_POWER_LINK, process_lost_power_link_state)
{
    if (!port) {
        POE_DEBUG("Port arg is NULL");
        return PORT_EVENT_IDLE;
    }

    POE_WARN("Physical connection lost for port %s. Will reinitialize the port",
             port->ifname);

    struct poed_payload *query = create_get_port_details_query(port->id);
    if (LLDP_POED_ERR_OK == sync_send_poed_request(query, "get_port_details")) {
        const struct poed_payload *status = NULL;
        if (0 == find_payload_by_key(query, "status", &status) &&
            PAYLOAD_VALUE_STRING == status->type) {
            POE_NOTICE("Port %d state is %s", port->id, status->value.val_str);
        } else
            POE_ERR("Failed to parse the poed payload for %s", port->ifname);
    } else
        POE_ERR("Failed to send get_port_details request for %s", port->ifname);
    free_payload(query);
    free(query);

    return PORT_EVENT_OK;
}

/* State handlers end */

static pthread_mutex_t port_mutex;

/**
 * push_if_link_update - Update the operational state of port, based on the link
 * change event
 * @ifname: network interface name
 * @event: link change event
 *
 * Returns 0 if the update was processed successfully, 1 otherwise.
 */
int push_if_link_update(const char *ifname, enum port_if_link_event event)
{
    struct port_state_machine *port = NULL;
    if (0 != get_port_context_by_ifname(ifname, &port)) {
        POE_ERR("Failed to find port %s by ifname", ifname);
        return 1;
    }

    POE_DEBUG("Received an %s event for %s interface",
              (PORT_IF_UP == event)
                  ? "IF_UP"
                  : ((PORT_IF_DOWN == event) ? "IF_DOWN" : "Unknown"),
              ifname);

    int status = 0;
    pthread_mutex_lock(&port_mutex);
    switch (event) {
    case PORT_IF_UP:
        port->if_up = true;
        break;
    case PORT_IF_DOWN:
        port->if_up = false;
        break;
    default:
        status = 1;
        break;
    }
    pthread_mutex_unlock(&port_mutex);

    return status;
}

/**
 * Flag set to true when there's a new pending LLDP update.
 */
static volatile bool has_lldp_update = false;

/**
 * Local request queue to be processed every time a new LLDP update is received.
 */
static struct queue lldp_request_queue;

/**
 * log_lldp_update - Log the neighbor update field for debug purposes
 * @update: caller-initialized update
 */
static void log_lldp_update(const struct port_neighbor_update *update)
{
    if (!update)
        return;

    POE_DEBUG("Neighbor port ID: %d", update->id);
    POE_DEBUG("PoE device type: %X", update->settings.poe_device_type);
    POE_DEBUG("MDI supported: %X", update->settings.mdi_supported);
    POE_DEBUG("MDI enabled: %X", update->settings.mdi_enabled);
    POE_DEBUG("MDI paircontrol: %X", update->settings.mdi_paircontrol);
    POE_DEBUG("PSE power pair: %X", update->settings.pse_power_pair);
    POE_DEBUG("PD class: %X", update->settings.pd_class);
    if (update->settings.power_type > LLDP_DOT3_POWER_8023AT_OFF) {
        POE_DEBUG("Power type: %X", update->settings.power_type);
        POE_DEBUG("Power source: %X", update->settings.power_source);
        POE_DEBUG("Power priority: %X", update->settings.power_priority);
        POE_DEBUG("PD requested power: %X", update->settings.pd_requested);
        POE_DEBUG("PSE allocated power: %X", update->settings.pse_allocated);
    }
    if (update->settings.power_type_ext > LLDP_DOT3_POWER_8023BT_OFF) {
        POE_DEBUG("PD 4PID: %X", update->settings.pd_4pid);
        POE_DEBUG("PD requested A: %X", update->settings.pd_requested_a);
        POE_DEBUG("PD requested B: %X", update->settings.pd_requested_b);
        POE_DEBUG("PSE allocated A: %X", update->settings.pse_allocated_a);
        POE_DEBUG("PSE allocated B: %X", update->settings.pse_allocated_b);
        POE_DEBUG("PSE status: %X", update->settings.pse_status);
        POE_DEBUG("PD status: %X", update->settings.pd_status);
        POE_DEBUG("PSE pairs ext: %X", update->settings.pse_pairs_ext);
        POE_DEBUG("Power class mode A: %X",
                  update->settings.power_class_mode_a);
        POE_DEBUG("Power class mode B: %X",
                  update->settings.power_class_mode_b);
        POE_DEBUG("PD power class ext: %X",
                  update->settings.pd_power_class_ext);
        POE_DEBUG("Power type ext: %X", update->settings.power_type_ext);
        POE_DEBUG("PD load: %X", update->settings.pd_load);
        POE_DEBUG("PSE max available power: %X",
                  update->settings.pse_max_available_power);
    }
}

/**
 * push_lldp_neighbor_update - Enqueue neighbor update to be processed
 * @ifname: network interface name
 * @config: neighbor Dot3 power settings (nullable)
 *
 * If the @config comes in as NULL, then this is treated as the neighbor was
 * deleted. Memory allocation for @config must be managed by the caller.
 *
 * Returns 0, LLDP_POED_ERR_OK, if successful, an lldp_poed_err otherwise.
 *
 * Note: the processing here is asynchronous, so the caller doesn't have to wait
 * for the whole propagation down to the driver to happen.
 */
int push_lldp_neighbor_update(const char *ifname,
                              const struct port_dot3_power_settings *config)
{
    if (!ifname)
        return LLDP_POED_ERR_INVALID_PARAM;

    struct port_state_machine *port = NULL;
    if (0 != get_port_context_by_ifname(ifname, &port)) {
        POE_ERR("Failed to find port %s by ifname", ifname);
        return LLDP_POED_ERR_GETPORTDETAILS_FAILED;
    }

    has_lldp_update = true;
    struct port_neighbor_update *update =
        malloc(sizeof(struct port_neighbor_update));
    if (!update)
        return LLDP_POED_ERR_INTERNAL_ERROR;

    update->id = port->id;
    if (config) {
        memcpy(&(update->settings), config,
               sizeof(struct port_dot3_power_settings));
        update->was_deleted = false;
    } else {
        update->was_deleted = true;
        POE_DEBUG("LLDP neighbor for port %s got deleted", ifname);
    }
    log_lldp_update(update);

    /**
     * Enqueue the update to be processed by the state machine thread.
     */
    struct linked_list *node = malloc(sizeof(struct linked_list));
    if (!node) {
        free(update);
        return LLDP_POED_ERR_INTERNAL_ERROR;
    }
    node->value = update;
    node->next = NULL;
    q_enqueue(&lldp_request_queue, node);

    return LLDP_POED_ERR_OK;
}

int med_to_dot3(const struct port_med_power_settings *med_config,
                struct port_dot3_power_settings *dot3_config)
{
    #define RET(val) ({return val; val;})

    memset(dot3_config, 0, sizeof(struct port_dot3_power_settings));

    dot3_config->poe_device_type =
        med_config->poe_device_type == LLDP_MED_POW_TYPE_PSE ?
                                       LLDP_DOT3_POWER_PSE   :
        med_config->poe_device_type == LLDP_MED_POW_TYPE_PD  ?
                                       LLDP_DOT3_POWER_PD    :
        RET(LLDP_POED_ERR_INVALID_PARAM);

    if (med_config->poe_device_type == LLDP_MED_POW_TYPE_PSE) {
        dot3_config->power_source =
            med_config->power_source == LLDP_MED_POW_SOURCE_UNKNOWN    ?
                                        LLDP_DOT3_POWER_SOURCE_UNKNOWN :
            med_config->power_source == LLDP_MED_POW_SOURCE_PRIMARY    ?
                                        LLDP_DOT3_POWER_SOURCE_PRIMARY :
            med_config->power_source == LLDP_MED_POW_SOURCE_BACKUP     ?
                                        LLDP_DOT3_POWER_SOURCE_BACKUP  :
            RET(LLDP_POED_ERR_INVALID_PARAM);
    } else {
        dot3_config->power_source =
            med_config->power_source == LLDP_MED_POW_SOURCE_UNKNOWN    ?
                                        LLDP_DOT3_POWER_SOURCE_UNKNOWN :
            med_config->power_source == LLDP_MED_POW_SOURCE_PSE        ?
                                        LLDP_DOT3_POWER_SOURCE_PSE     :
            med_config->power_source == LLDP_MED_POW_SOURCE_LOCAL      ?
                                        LLDP_DOT3_POWER_SOURCE_LOCAL   :
            med_config->power_source == LLDP_MED_POW_SOURCE_BOTH       ?
                                        LLDP_DOT3_POWER_SOURCE_BOTH    :
            RET(LLDP_POED_ERR_INVALID_PARAM);
    }

    dot3_config->power_priority =
        med_config->power_priority == LLDP_MED_POW_PRIO_UNKNOWN     ?
                                      LLDP_DOT3_POWER_PRIO_UNKNOWN  :
        med_config->power_priority == LLDP_MED_POW_PRIO_CRITICAL    ?
                                      LLDP_DOT3_POWER_PRIO_CRITICAL :
        med_config->power_priority == LLDP_MED_POW_PRIO_HIGH        ?
                                      LLDP_DOT3_POWER_PRIO_HIGH     :
        med_config->power_priority == LLDP_MED_POW_PRIO_LOW         ?
                                      LLDP_DOT3_POWER_PRIO_LOW      :
        RET(LLDP_POED_ERR_INVALID_PARAM);

    #undef RET

    /**
     * Map MED to dot3ab
     */
    dot3_config->power_type_ext = LLDP_DOT3_POWER_8023BT_OFF;
    dot3_config->pd_requested = med_config->value;
    dot3_config->power_type = LLDP_DOT3_POWER_8023AT_TYPE2;
    /**
     * 802.1ab fields that are not transmitted by a PD and hence set to -1: MDI
     * power support, MDI power state, PSE pairs control and PSE power pair and
     * PD power class.
     */
    dot3_config->mdi_supported = -1;
    dot3_config->mdi_enabled = -1;
    dot3_config->mdi_paircontrol = -1;
    dot3_config->pse_power_pair = -1;
    dot3_config->pd_class = -1;

    return LLDP_POED_ERR_OK;
}

int dot3_to_med(const struct port_dot3_power_settings *dot3_config,
                struct port_med_power_settings *med_config)
{
    #define RET(val) ({return val; val;})

    med_config->poe_device_type =
        dot3_config->poe_device_type == LLDP_DOT3_POWER_PSE   ?
                                        LLDP_MED_POW_TYPE_PSE :
        dot3_config->poe_device_type == LLDP_DOT3_POWER_PD    ?
                                        LLDP_MED_POW_TYPE_PD  :
        RET(LLDP_POED_ERR_INVALID_PARAM);

    if (med_config->poe_device_type == LLDP_MED_POW_TYPE_PSE) {
        med_config->power_source =
            dot3_config->power_source == LLDP_DOT3_POWER_SOURCE_UNKNOWN ?
                                         LLDP_MED_POW_SOURCE_UNKNOWN    :
            dot3_config->power_source == LLDP_DOT3_POWER_SOURCE_PRIMARY ?
                                         LLDP_MED_POW_SOURCE_PRIMARY    :
            dot3_config->power_source == LLDP_DOT3_POWER_SOURCE_BACKUP  ?
                                         LLDP_MED_POW_SOURCE_BACKUP     :
            RET(LLDP_POED_ERR_INVALID_PARAM);
    } else {
        med_config->power_source =
            dot3_config->power_source == LLDP_DOT3_POWER_SOURCE_UNKNOWN ?
                                         LLDP_MED_POW_SOURCE_UNKNOWN    :
            dot3_config->power_source == LLDP_DOT3_POWER_SOURCE_PSE     ?
                                         LLDP_MED_POW_SOURCE_PSE        :
            dot3_config->power_source == LLDP_DOT3_POWER_SOURCE_LOCAL   ?
                                         LLDP_MED_POW_SOURCE_LOCAL      :
            dot3_config->power_source == LLDP_DOT3_POWER_SOURCE_BOTH    ?
                                         LLDP_MED_POW_SOURCE_BOTH       :
            RET(LLDP_POED_ERR_INVALID_PARAM);
    }

    med_config->power_priority =
        dot3_config->power_priority == LLDP_DOT3_POWER_PRIO_UNKNOWN  ?
                                       LLDP_MED_POW_PRIO_UNKNOWN     :
        dot3_config->power_priority == LLDP_DOT3_POWER_PRIO_CRITICAL ?
                                       LLDP_MED_POW_PRIO_CRITICAL    :
        dot3_config->power_priority == LLDP_DOT3_POWER_PRIO_HIGH     ?
                                       LLDP_MED_POW_PRIO_HIGH        :
        dot3_config->power_priority == LLDP_DOT3_POWER_PRIO_LOW      ?
                                       LLDP_MED_POW_PRIO_LOW         :
        RET(LLDP_POED_ERR_INVALID_PARAM);

    #undef RET

    med_config->value = dot3_config->pse_allocated;

    return LLDP_POED_ERR_OK;
}

/**
 * fill_port_range - Fill the port range based on the current port count
 * @pr: caller-allocated port range
 *
 * Returns 0, LLDP_POED_ERR_OK, if successful, an lldp_poed_err otherwise.
 */
static int fill_port_range(struct port_range *pr)
{
    if (!pr || !ports.p)
        return LLDP_POED_ERR_INVALID_PARAM;

    memset(pr, 0, sizeof(struct port_range));
    strncpy(pr->ifname_prefix, PORT_INTERFACE_NAME_PREFIX, IFNAME_PREFIX_SIZE);
    /**
     * Assumptions is that all ports are spread contiguously.
     * If the ports are interleaved with non-PoE ports, the behavior is
     * undefined.
     */
    pr->start_index = 1;
    pr->end_index = ports.size;

    return LLDP_POED_ERR_OK;
}

/**
 * wait_for_poed_response - Write the @message into the named pipe and then poll
 * for the response, retrying if there was an error along the way. The response
 * buffer is copied back in @message
 * @message: used for both for sending the request and copying back the response
 *
 * Returns 0 if successful. This function will always retry to send and receive
 * a response from poed.
 */
static int wait_for_poed_response(char *message, size_t message_len)
{
    /**
     * Number of milliseconds to wait for the poed response through polling.
     */
    static const size_t poed_reply_timeout_ms = 5000U;

    /**
     * Write the request in blocking mode first and then poll for the reply
     * from the poed, having a pre-defined timeout.
     */
    int write_fd = open(WRITE_FIFO_PATH, O_WRONLY);
    if (write_fd < 0) {
        POE_ERR("Failed to open the write FIFO: %s", strerror(errno));
        return 1;
    }
    message[message_len - 1] = '\0';
    if (write(write_fd, message, strlen(message)) < 0) {
        POE_ERR("Failed to write to FIFO: %s", strerror(errno));
        close(write_fd);
        return 1;
    }
    close(write_fd);

    int read_fd = open(READ_FIFO_PATH, O_RDONLY | O_NONBLOCK);
    if (read_fd < 0) {
        POE_ERR("Failed to open the read FIFO: %s", strerror(errno));
        return 1;
    }
    struct pollfd waiter = {
        .fd = read_fd,
        .events = POLLIN,
    };
    while (1) {
        int status = poll(&waiter, 1, poed_reply_timeout_ms);
        switch (status) {
        case 0:
            POE_DEBUG("Poed reply timed out. "
                    "Retrying...");
            break;
        case 1:
            if (waiter.revents & POLLIN) {
                ssize_t ret = read(waiter.fd, message, POED_MESSAGE_MAX_SIZE);
                if (ret > 0) {
                    /**
                     * Got the response, can exit now.
                     */
                    goto success;
                }
                while (-1 == ret && EINTR == errno) {
                    /**
                     * Syscall got interrupted. Must retry.
                     */
                    ret = read(waiter.fd, message, POED_MESSAGE_MAX_SIZE);
                }
                if (-1 == ret && EAGAIN == errno) {
                    /**
                     * Retry polling the FD again.
                     */
                    continue;
                } else if (0 == ret) /* Connection was closed. */
                {
                    POE_ERR("Received EOF");
                    goto fail;
                }
                goto success;
            } else /* POLLERR or POLLHUP */
            {
                POE_ERR("Read pipe is in invalid state "
                        "(not open or closed prematurely)");
                goto fail;
            }
            break;
        default:
            POE_ERR("Unexpected poed polling error: %s", strerror(errno));
            goto fail;
        }
    }

success:
    close(read_fd);
    return 0;

fail:
    close(read_fd);
    return 1;
}

/**
 * Number of microseconds to wait for a retry, in case the poed request failed.
 */
static const useconds_t poed_retry_interval_us = 1000000U;

/**
 * sync_send_poed_request - Send a synchronous request to poed, waiting for the
 * response
 * @query: query params to be copied to the JSON-RPC message. This is going
 * to be used for filling back the response from poed
 * @method: JSON-RPC method
 *
 * @warning: caller has the responsibilty to free the @query
 *
 * Returns 0, LLDP_POED_ERR_OK, if successful, an lldp_poed_err otherwise.
 *
 * Note: the request ID is incremented automatically and must match the ID from
 * the poed reply.
 */
static int sync_send_poed_request(struct poed_payload *query,
                                  const char *method)
{
    if (!query || !method)
        return 1;

    POE_DEBUG("Sending request for %s method", method);
    log_payload(query);

    char json_rpc_message[POED_MESSAGE_MAX_SIZE];
    memset(json_rpc_message, '\0', POED_MESSAGE_MAX_SIZE);
    ssize_t request_id;
    if (0 != payload_to_json_rpc(query, method, &request_id, json_rpc_message,
                                 POED_MESSAGE_MAX_SIZE)) {
        POE_ERR("Unable to create the JSON-RPC request "
                "for %s",
                method);
        log_payload(query);
        return LLDP_POED_ERR_SERIALIZE_ERROR;
    }

    while (0 != wait_for_poed_response(json_rpc_message, POED_MESSAGE_MAX_SIZE)) {
        POE_WARN("Retrying...");
        usleep(poed_retry_interval_us);
    }

    free_payload(query); /* Reuse the same query. */
    if (0 != json_rpc_to_payload(json_rpc_message, POED_MESSAGE_MAX_SIZE,
                                 request_id, query)) {
        POE_ERR("Unable to parse the JSON-RPC response "
                "for %s",
                method);
        return LLDP_POED_ERR_SERIALIZE_ERROR;
    }

    POE_DEBUG("Received valid response from poed for %s method", method);
    log_payload(query);

    return LLDP_POED_ERR_OK;
}

/**
 * init_ports - Query poed to determine the number of ports and determine the
 * already disabled/enabled PoE ports.
 *
 * Returns 0, LLDP_POED_ERR_OK, if successful, an lldp_poed_err otherwise.
 */
static int init_ports(void)
{
    struct poed_payload port_query = {
        .type = PAYLOAD_VALUE_OBJECT,
        .child_count = 0, /* No request params. */
        .children = NULL,
    };
    if (0 != sync_send_poed_request(&port_query, "get_disabled_ports")) {
        POE_ERR("Failed to send the request for detecting the PoE ports");
        free_payload(&port_query);
        return LLDP_POED_ERR_SEND_REQUEST_FAILED;
    }

    /**
     * Parse the payload and initialize all ports.
     */
    const struct poed_payload *port_count = NULL;
    const struct poed_payload *disabled_ports = NULL;
    const struct poed_payload *lldp_disabled_ports = NULL;
    if (0 == find_payload_by_key(&port_query, "ports_total_count",
                                 &port_count) &&
        0 == find_payload_by_key(&port_query, "disabled_ports",
                                 &disabled_ports) &&
        0 == find_payload_by_key(&port_query, "lldp_disabled_ports",
                                 &lldp_disabled_ports)) {
        if (!(PAYLOAD_VALUE_NUMBER == port_count->type &&
              (PAYLOAD_VALUE_ARRAY == disabled_ports->type ||
               PAYLOAD_VALUE_NULL == disabled_ports->type) &&
              (PAYLOAD_VALUE_ARRAY == lldp_disabled_ports->type ||
               PAYLOAD_VALUE_NULL == lldp_disabled_ports->type))) {
            POE_ERR("Invalid payload type");
            goto parsing_failed;
        }

        /**
         * Initialize, by default, all ports to WAIT_LINK, having no
         * active data link.
         */
        ports.size = port_count->value.val_int;
        ports.p = malloc(ports.size * sizeof(struct port_state_machine));
        FOR_I_IN(0, ports.size - 1)
        {
            int port_id = i + 1;
            ports.p[i].id = port_id;
            snprintf(ports.p[i].ifname, IFNAMSIZ, "%s%d",
                     PORT_INTERFACE_NAME_PREFIX, port_id);
            ports.p[i].admin_lldp_enabled = true;
            ports.p[i].lldp_default_pwr_limit_update_pending = false;
            ports.p[i].if_up = false;
            ports.p[i].timeout_time = 0;
            ports.p[i].current_state = PORT_WAIT_PD;
            ports.p[i].process_state = state_handlers[PORT_WAIT_PD];
        }
        if (PAYLOAD_VALUE_ARRAY == disabled_ports->type) {
            /**
             * Override disabled ports and transition to PORT_DISABLED.
             */
            const struct poed_payload *port_it = NULL;
            FOR_EACH(port_it, disabled_ports->children,
                     disabled_ports->child_count)
            {
                if (PAYLOAD_VALUE_NUMBER != port_it->type) {
                    POE_ERR("Invalid payload type");
                    goto parsing_failed;
                }

                struct port_state_machine *port = NULL;
                if (0 !=
                    get_port_context_by_id(port_it->value.val_int, &port)) {
                    POE_ERR("Failed to find port by ID: %d",
                            port_it->value.val_int);
                    goto parsing_failed;
                }
                port->current_state = PORT_DISABLED;
                port->process_state = state_handlers[PORT_DISABLED];
            }
        }
        if (PAYLOAD_VALUE_ARRAY == lldp_disabled_ports->type) {
            /**
             * Reflect the LLDP admin state set by the user.
             */
            const struct poed_payload *port_it = NULL;
            FOR_EACH(port_it, disabled_ports->children,
                     disabled_ports->child_count)
            {
                if (PAYLOAD_VALUE_NUMBER != port_it->type) {
                    POE_ERR("Invalid payload type");
                    goto parsing_failed;
                }

                struct port_state_machine *port = NULL;
                if (0 !=
                    get_port_context_by_id(port_it->value.val_int, &port)) {
                    POE_ERR("Failed to find port by ID: %d",
                            port_it->value.val_int);
                    goto parsing_failed;
                }
                port->admin_lldp_enabled = false;
            }
        }
    } else
        goto parsing_failed;

    POE_NOTICE("State machine was initialized successfully for all ports");
    free_payload(&port_query);

    return LLDP_POED_ERR_OK;

parsing_failed:
    POE_ERR("Failed to parse the poed payload for detecting the "
            "PoE ports");
    free_payload(&port_query);
    return LLDP_POED_ERR_PARSE_ERROR;
}

/**
 * create_poed_fifo - Creates the named pipe FIFO to communicate with the poed
 * agent.
 *
 * Returns 0 if successful, otherwise 1.
 */
static int create_poed_fifo(void)
{
    /* Create the FIFOs with the poed agent, if inexistent. */
    int status = mkfifo(READ_FIFO_PATH, 0600);
    if (status != 0) {
        if (errno != EEXIST) {
            POE_ERR("Failed to create " READ_FIFO_PATH " FIFO: %s",
                    strerror(errno));
            return 1;
        }
        POE_WARN("LLDP-POED <-> POED read FIFO exists, not creating one");
    }
    status = mkfifo(WRITE_FIFO_PATH, 0600);
    if (status != 0) {
        if (errno != EEXIST) {
            POE_ERR("Failed to create " WRITE_FIFO_PATH " FIFO: %s",
                    strerror(errno));
            return 1;
        }
        POE_WARN("LLDP-POED <-> POED write FIFO exists, not creating one");
    }

    return 0;
}

/**
 * process_port - Process the port state machine by calling the state handler
 * and setting the new state based on the handler generated event
 * @port: port state machine to be processed
 * @data: optional data arg
 */
static void process_port(struct port_state_machine *port, const void *data)
{
    if (!port)
        return;

    enum port_state_event ev = port->process_state(port, data);
    if (PORT_EVENT_IDLE == ev) {
        POE_DEBUG("Port %s remained in %s", port->ifname,
                  port_state_string[port->current_state]);
        return; /* Skip updating the state if IDLE. */
    }

    enum port_state prev_state = port->current_state;
    enum port_state next_state = port_transition_table[port->current_state][ev];
    port->current_state = next_state;
    port->process_state = state_handlers[next_state];
    POE_INFO("Port %s went to state %s from %s", port->ifname,
             port_state_string[next_state], port_state_string[prev_state]);
}

static const useconds_t port_state_machine_sleep_time = 1000000U;

/**
 * handle_port_state_machine - Process each port state machine and incoming
 * neighbor updates from lldpctl
 *
 * Process each state machine one by one, by calling each port's assigned state
 * handler (each possible state corresponds to one unique state handler).
 * Other threads may interact with this one in order to push updates (either
 * link changes or LLDP neighbor updates). This may, in turn, trigger state
 * changes. Acts similarly to a work queue for all ports, which calls each state
 * handler and maps the returned event to a certain state, if the transition is
 * valid. Illegal transitions will render the port in PORT_INVALID_STATE
 * indefinitely.
 */
void *handle_port_state_machine()
{
    q_init(&lldp_request_queue, true);
    pthread_mutex_init(&port_mutex, NULL);
    init_transition_table();

    if (0 != create_poed_fifo()) {
        POE_CRIT("Unavailable poed FIFO. Exiting..");
        return NULL;
    }

    if (0 != init_ports()) {
        POE_CRIT(
            "Failed to initialize the port state machine array. Exiting..");
        return NULL;
    }

    struct port_range pr;
    if (0 != fill_port_range(&pr)) {
        POE_CRIT("Failed to initialize the port range structure");
        return NULL;
    }
    /**
     * Trigger an IF_UP event for all operationally up ports.
     */
    netlink_scan_all_ports(&pr);

    while (!thread_exit) {
        if (has_lldp_update) {
            int count_processed = 0;
            struct linked_list *node = NULL;
            /**
             * Process all enqueued updates, passing the neighbor data if the
             * port is in PORT_WAIT_LLDP_REQ.
             */
            while (NULL != (node = q_dequeue(&lldp_request_queue))) {
                struct port_neighbor_update *update = node->value;
                struct port_state_machine *port = NULL;
                if (0 != get_port_context_by_id(update->id, &port)) {
                    POE_ERR("Failed to find port by ID: %d", update->id);
                    POE_WARN("Ignoring neighbor update for port %d",
                             update->id);
                } else {
                    if (PORT_WAIT_LLDP_REQ != port->current_state &&
                        PORT_DEFAULT_PWR_LIMIT != port->current_state &&
                        PORT_L2_NEG_COMPLETE != port->current_state) {
                        POE_WARN("Ignoring neighbor "
                                 "update. %s is not waiting for LLDP updates",
                                 port->ifname);
                    } else {
                        process_port(port, update);
                        count_processed++;
                    }
                }
                free(update);
                free(node);
            }
            has_lldp_update = false;
            POE_DEBUG("Processed %d LLDP neighbor updates", count_processed);
        }

        /**
         * Process each separate port state machine, after previously treating
         * updates that were pending.
         */
        struct port_state_machine *port_it = NULL;
        FOR_EACH(port_it, ports.p, ports.size) { process_port(port_it, NULL); }

        usleep(port_state_machine_sleep_time);
    }

    POE_NOTICE("Exiting handle_port_state_machine gracefully");

    free(ports.p);
    q_destroy(&lldp_request_queue);

    return NULL;
}
