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

#include <limits.h>
#include <lldp-const.h>
#include <lldpctl.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>
#include <time.h>

#include "include/common.h"
#include "include/lldp_event_handler.h"
#include "include/lldp_poed_err.h"
#include "include/logger.h"

static lldpctl_conn_t *lldpctl_conn = NULL;

/**
 * get_local_ttl - Compute the TTL used for outgoing LLDPDUs.
 * @ttl: computed TTL value
 *
 * Returns 0 if successful, 1 otherwise.
 */
static int get_local_ttl(int *ttl)
{
    if (!ttl)
        return 1;

    lldpctl_atom_t *config = lldpctl_get_configuration(lldpctl_conn);
    if (!config) {
        POE_ERR("Failed to get the global lldpd config: %s",
                lldpctl_last_strerror(lldpctl_conn));
        return 1;
    }
    int tx_hold = lldpctl_atom_get_int(config, lldpctl_k_config_tx_hold);
    /* lldpctl_k_config_tx_interval counterpart is derived from this one */
    int tx_interval =
        lldpctl_atom_get_int(config, lldpctl_k_config_tx_interval_ms);

    /**
     * Output seconds, rounding to the next second.
     */
    *ttl = ((((long) tx_interval) * tx_hold) + 999) / 1000;

    lldpctl_atom_dec_ref(config);

    return 0;
}

/**
 * read_med_power - Read all MED atom keys for the given port
 * @port: port atom to read from
 * @config: output config values
 * @ifname: network interface name associated to the remote port
 *
 * The ANSI/TIA-1057 (LLDP-MED) are required. If either one is absent,
 * return an error.
 *
 * Returns 0, LLDP_POED_ERR_OK, if successful, an error_code otherwise.
 */
static int read_med_power(lldpctl_atom_t *port,
                           struct port_med_power_settings *config,
                           const char *ifname)
{
    if (!port || !config || !ifname)
        return LLDP_POED_ERR_INVALID_PARAM;

    lldpctl_atom_t *med_power =
        lldpctl_atom_get(port, lldpctl_k_port_med_power);
    if (!med_power) {
        POE_ERR("Unable to retrieve the MED power atom for "
                "%s: %s",
                ifname, lldpctl_last_strerror(lldpctl_conn));
        return LLDP_POED_ERR_MED_POWER_ATOM_FAILED;
    }

    int status = LLDP_POED_ERR_OK;
    config->poe_device_type =
        lldpctl_atom_get_int(med_power, lldpctl_k_med_power_type);
    if (LLDP_MED_POW_TYPE_PD != config->poe_device_type) {
        POE_ERR("LLDP MED PoE device type is not "
                "valid for %s",
                ifname);
        status = LLDP_POED_ERR_MED_POWER_ATOM_FAILED;
        goto fail;
    }

    /**
     * Read ANSI/TIA-1057 (LLDP-MED) fields, if available (given the Power Type field).
     */
    config->power_source =
        lldpctl_atom_get_int(med_power, lldpctl_k_med_power_source);
    config->power_priority =
        lldpctl_atom_get_int(med_power, lldpctl_k_med_power_priority);
    /**
     * @warning: for some reason, liblldpctl will work in mW,
     * instead of 0.1W (as recommended by the standard).
     * Therefore, convert to 0.1W for interop with the PoE controller,
     * which is 802.3at/bt compliant.
     */
    config->value =
        lldpctl_atom_get_int(med_power, lldpctl_k_med_power_val) / 100;

    POE_INFO("Successfully read LLDP MED power for %s", ifname);
    lldpctl_atom_dec_ref(med_power);

    return status;

fail:
    lldpctl_atom_dec_ref(med_power);
    return status;
}

/**
 * read_dot3_power - Read all Dot3 atom keys for the given port
 * @port: port atom to read from
 * @config: output config values
 * @ifname: network interface name associated to the remote port
 *
 * The 802.1ab and 802.3at fields are required. If either one is absent,
 * return an error.
 *
 * Returns 0, LLDP_POED_ERR_OK, if successful, an error_code otherwise.
 */
static int read_dot3_power(lldpctl_atom_t *port,
                           struct port_dot3_power_settings *config,
                           const char *ifname)
{
    if (!port || !config || !ifname)
        return LLDP_POED_ERR_INVALID_PARAM;

    lldpctl_atom_t *dot3_power =
        lldpctl_atom_get(port, lldpctl_k_port_dot3_power);
    if (!dot3_power) {
        POE_ERR("Unable to retrieve the Dot3 power atom for "
                "%s: %s",
                ifname, lldpctl_last_strerror(lldpctl_conn));
        return LLDP_POED_ERR_DOT3_POWER_ATOM_FAILED;
    }

    int status = LLDP_POED_ERR_OK;
    config->poe_device_type =
        lldpctl_atom_get_int(dot3_power, lldpctl_k_dot3_power_devicetype);
    if (LLDP_DOT3_POWER_PD != config->poe_device_type) {
        POE_ERR("LLDP Dot3 PoE device type is not "
                "valid for %s",
                ifname);
        status = LLDP_POED_ERR_DOT3_POWER_ATOM_FAILED;
        goto fail;
    }

    /**
     * 802.1ab fields that are not transmitted by a PD and hence set to -1: MDI
     * power support, MDI power state, PSE pairs control and PSE power pair and
     * PD power class.
     */
    config->mdi_supported = -1;
    config->mdi_enabled = -1;
    config->mdi_paircontrol = -1;
    config->pse_power_pair = -1;
    config->pd_class = -1;

    /**
     * Read 802.3at PoE fields, if available (given the Power Type field).
     */
    config->power_type =
        lldpctl_atom_get_int(dot3_power, lldpctl_k_dot3_power_type);
    if (LLDP_DOT3_POWER_8023AT_OFF == config->power_type) {
        POE_ERR("LLDP Dot3 DLL classification fields are not "
                "available for %s",
                ifname);
        status = LLDP_POED_ERR_DOT3_POWER_ATOM_FAILED;
        goto fail;
    }
    config->power_source =
        lldpctl_atom_get_int(dot3_power, lldpctl_k_dot3_power_source);
    config->power_priority =
        lldpctl_atom_get_int(dot3_power, lldpctl_k_dot3_power_priority);
    /**
     * @warning: for some reason, liblldpctl will work in mW,
     * instead of 0.1W (as recommended by the standard).
     * Therefore, convert to 0.1W for interop with the PoE controller,
     * which is 802.3at/bt compliant.
     */
    config->pd_requested =
        lldpctl_atom_get_int(dot3_power, lldpctl_k_dot3_power_requested) / 100;
    config->pse_allocated =
        lldpctl_atom_get_int(dot3_power, lldpctl_k_dot3_power_allocated) / 100;

    /**
     * Read 802.3bt PoE fields, if available (given the Power Type ext field).
     */
    config->power_type_ext =
        lldpctl_atom_get_int(dot3_power, lldpctl_k_dot3_power_type_ext);
    if (LLDP_DOT3_POWER_8023BT_OFF == config->power_type_ext) {
        POE_WARN("LLDP Dot3 802.3bt fields are not available "
                 "for %s",
                 ifname);
    } else {
        config->pd_4pid =
            lldpctl_atom_get_int(dot3_power, lldpctl_k_dot3_power_pd_4pid);
        config->pd_requested_a =
            lldpctl_atom_get_int(dot3_power, lldpctl_k_dot3_power_requested_a) /
            100;
        config->pd_requested_b =
            lldpctl_atom_get_int(dot3_power, lldpctl_k_dot3_power_requested_b) /
            100;
        config->pse_allocated_a =
            lldpctl_atom_get_int(dot3_power, lldpctl_k_dot3_power_allocated_a) /
            100;
        config->pse_allocated_b =
            lldpctl_atom_get_int(dot3_power, lldpctl_k_dot3_power_allocated_b) /
            100;
        config->pse_status =
            lldpctl_atom_get_int(dot3_power, lldpctl_k_dot3_power_pse_status);
        config->pd_status =
            lldpctl_atom_get_int(dot3_power, lldpctl_k_dot3_power_pd_status);
        config->pse_pairs_ext = lldpctl_atom_get_int(
            dot3_power, lldpctl_k_dot3_power_pse_pairs_ext);
        config->power_class_mode_a =
            lldpctl_atom_get_int(dot3_power, lldpctl_k_dot3_power_class_a);
        config->power_class_mode_b =
            lldpctl_atom_get_int(dot3_power, lldpctl_k_dot3_power_class_b);
        config->pd_power_class_ext =
            lldpctl_atom_get_int(dot3_power, lldpctl_k_dot3_power_class_ext);
        config->pd_load =
            lldpctl_atom_get_int(dot3_power, lldpctl_k_dot3_power_pd_load);
        config->pse_max_available_power =
            lldpctl_atom_get_int(dot3_power, lldpctl_k_dot3_power_pse_max);

        /**
         * These fields must be set to zero by the PD, according to the 802.3bt
         * standard.
         */
        if (config->pse_status) {
            POE_WARN("PSE powering status field "
                     "was not set to zero by the PD for %s",
                     ifname);
        } else if (config->pse_pairs_ext) {
            POE_WARN("PSE power pairs ext field "
                     "was not set to zero by the PD for %s",
                     ifname);
        } else if (config->pse_max_available_power) {
            POE_WARN("PSE maximum available power "
                     "value field was not set to zero by the PD for %s",
                     ifname);
        }
    }

    POE_INFO("Successfully read LLDP Dot3 power for %s", ifname);
    lldpctl_atom_dec_ref(dot3_power);

    return status;

fail:
    lldpctl_atom_dec_ref(dot3_power);
    return status;
}

/**
 * write_med_power - Update the port MED atom config
 * @port: port atom to write to
 * @config: input config values
 * @ifname: network interface name associated to the remote port
 *
 * If writing the MED power atom is successful, this function will set the new
 * MED power to the port atom, thus transmitting the update to the LLDP
 * neighbor.
 *
 * Returns 0, LLDP_POED_ERR_OK, if successful, an error_code otherwise.
 */
static int write_med_power(lldpctl_atom_t *port,
                           const struct port_med_power_settings *config,
                           const char *ifname)
{
    if (!port || !config || !ifname)
        return LLDP_POED_ERR_INVALID_PARAM;

    if (LLDP_MED_POW_TYPE_PSE != config->poe_device_type) {
        POE_ERR("Invalid MED input config for %s", ifname);
        return LLDP_POED_ERR_MED_POWER_ATOM_FAILED;
    }

    lldpctl_atom_t *med_power =
        lldpctl_atom_get(port, lldpctl_k_port_med_power);
    if (!med_power) {
        POE_ERR("Unable to retrieve the MED power atom for "
                "%s: %s",
                ifname, lldpctl_last_strerror(lldpctl_conn));
        return LLDP_POED_ERR_MED_POWER_ATOM_FAILED;
    }

    int status = LLDP_POED_ERR_OK;
    const char *to_set = NULL;
    if (NULL == (to_set = "MED PoE device type",
                 lldpctl_atom_set_int(med_power, lldpctl_k_med_power_type,
                                      config->poe_device_type)) ||
        NULL == (to_set = "MED Power source",
                 lldpctl_atom_set_int(med_power, lldpctl_k_med_power_source,
                                      config->power_source)) ||
        NULL == (to_set = "MED Power priority",
                 lldpctl_atom_set_int(med_power, lldpctl_k_med_power_priority,
                                      config->power_priority)) ||
        NULL == (to_set = "MED Power value",
                 lldpctl_atom_set_int(med_power, lldpctl_k_med_power_val,
                                      config->value * 100))) {
        POE_ERR("Failed to set %s for %s", to_set, ifname);
        status = LLDP_POED_ERR_MED_POWER_ATOM_FAILED;
        goto fail;
    }

    if (lldpctl_atom_set(port, lldpctl_k_port_med_power, med_power)) {
        POE_INFO("Successfully transmitted the LLDP MED "
                 "power settings for %s",
                 ifname);
        lldpctl_atom_dec_ref(med_power);
    } else {
        POE_ERR("Failed to transmit the LLDP MED "
                "power settings for %s",
                ifname);
        status = LLDP_POED_ERR_MED_POWER_ATOM_FAILED;
        goto fail;
    }

    return status;

fail:
    lldpctl_atom_dec_ref(med_power);
    return status;
}

/**
 * write_dot3_power - Update the port Dot3 atom config
 * @port: port atom to write to
 * @config: input config values
 * @ifname: network interface name associated to the remote port
 *
 * If writing the Dot3 power atom is successful, this function will set the new
 * Dot3 power to the port atom, thus transmitting the update to the LLDP
 * neighbor.
 *
 * Returns 0, LLDP_POED_ERR_OK, if successful, an error_code otherwise.
 */
static int write_dot3_power(lldpctl_atom_t *port,
                            const struct port_dot3_power_settings *config,
                            const char *ifname)
{
    if (!port || !config || !ifname)
        return LLDP_POED_ERR_INVALID_PARAM;

    lldpctl_atom_t *dot3_power =
        lldpctl_atom_get(port, lldpctl_k_port_dot3_power);
    if (!dot3_power) {
        POE_ERR("Unable to retrieve the Dot3 power atom for "
                "%s: %s",
                ifname, lldpctl_last_strerror(lldpctl_conn));
        return LLDP_POED_ERR_DOT3_POWER_ATOM_FAILED;
    }

    /**
     * PSE must be able to fill in all fields, at least the 802.3at ones.
     */
    int status = LLDP_POED_ERR_OK;
    if (LLDP_DOT3_POWER_PSE != config->poe_device_type ||
        LLDP_DOT3_POWER_8023AT_OFF == config->power_type) {
        POE_ERR("Invalid Dot3 input config for %s", ifname);
        status = LLDP_POED_ERR_DOT3_POWER_ATOM_FAILED;
        goto fail;
    }

    /**
     * Following keys are required to be set by the PSE. If any of them fails,
     * then return with error.
     */
    /**
     * @warning: for some reason, liblldpctl will work in mW,
     * instead of 0.1W (as recommended by the standard).
     * Therefore, convert to mW from 0.1W.
     */
    const char *to_set = NULL;
    if (NULL ==
            (to_set = "PoE device type",
             lldpctl_atom_set_int(dot3_power, lldpctl_k_dot3_power_devicetype,
                                  config->poe_device_type)) ||
        NULL ==
            (to_set = "MDI power support",
             lldpctl_atom_set_int(dot3_power, lldpctl_k_dot3_power_supported,
                                  config->mdi_supported)) ||
        NULL == (to_set = "MDI power state (enabled/disabled)",
                 lldpctl_atom_set_int(dot3_power, lldpctl_k_dot3_power_enabled,
                                      config->mdi_enabled)) ||
        NULL ==
            (to_set = "PSE pair control",
             lldpctl_atom_set_int(dot3_power, lldpctl_k_dot3_power_paircontrol,
                                  config->mdi_paircontrol)) ||
        NULL == (to_set = "PSE power pair",
                 lldpctl_atom_set_int(dot3_power, lldpctl_k_dot3_power_pairs,
                                      config->pse_power_pair)) ||
        NULL == (to_set = "PD power class",
                 lldpctl_atom_set_int(dot3_power, lldpctl_k_dot3_power_class,
                                      config->pd_class)) ||
        NULL == (to_set = "Power type",
                 lldpctl_atom_set_int(dot3_power, lldpctl_k_dot3_power_type,
                                      config->power_type)) ||
        NULL == (to_set = "Power source",
                 lldpctl_atom_set_int(dot3_power, lldpctl_k_dot3_power_source,
                                      config->power_source)) ||
        NULL == (to_set = "Power priority",
                 lldpctl_atom_set_int(dot3_power, lldpctl_k_dot3_power_priority,
                                      config->power_priority)) ||
        NULL ==
            (to_set = "PD requested power value",
             lldpctl_atom_set_int(dot3_power, lldpctl_k_dot3_power_requested,
                                  config->pd_requested * 100)) ||
        NULL ==
            (to_set = "PSE allocated power value",
             lldpctl_atom_set_int(dot3_power, lldpctl_k_dot3_power_allocated,
                                  config->pse_allocated * 100))) {
        POE_ERR("Failed to set %s for %s", to_set, ifname);
        status = LLDP_POED_ERR_DOT3_POWER_ATOM_FAILED;
        goto fail;
    }

    if (LLDP_DOT3_POWER_8023BT_OFF != config->power_type_ext) {
        if (NULL == (to_set = "PD 4PID (to zero)",
                     lldpctl_atom_set_int(dot3_power,
                                          lldpctl_k_dot3_power_pd_4pid, 0)) ||
            (USHRT_MAX !=
                 config->pd_requested_a && /* Not set for single-signature. */
             NULL == (to_set = "PD requested power value mode A",
                      lldpctl_atom_set_int(dot3_power,
                                           lldpctl_k_dot3_power_requested_a,
                                           config->pd_requested_a * 100))) ||
            (USHRT_MAX !=
                 config->pd_requested_b && /* Not set for single-signature. */
             NULL == (to_set = "PD requested power value mode B",
                      lldpctl_atom_set_int(dot3_power,
                                           lldpctl_k_dot3_power_requested_b,
                                           config->pd_requested_b * 100))) ||
            (USHRT_MAX !=
                 config->pse_allocated_a && /* Not set for single-signature. */
             NULL == (to_set = "PSE allocated power value mode A",
                      lldpctl_atom_set_int(dot3_power,
                                           lldpctl_k_dot3_power_allocated_a,
                                           config->pse_allocated_a * 100))) ||
            (USHRT_MAX !=
                 config->pse_allocated_b && /* Not set for single-signature. */
             NULL == (to_set = "PSE allocated power value mode B",
                      lldpctl_atom_set_int(dot3_power,
                                           lldpctl_k_dot3_power_allocated_b,
                                           config->pse_allocated_b * 100))) ||
            NULL == (to_set = "PSE powering status",
                     lldpctl_atom_set_int(dot3_power,
                                          lldpctl_k_dot3_power_pse_status,
                                          config->pse_status)) ||
            NULL == (to_set = "PD powered status (to zero)",
                     lldpctl_atom_set_int(dot3_power,
                                          lldpctl_k_dot3_power_pd_status, 0)) ||
            NULL == (to_set = "PSE power pairs ext",
                     lldpctl_atom_set_int(dot3_power,
                                          lldpctl_k_dot3_power_pse_pairs_ext,
                                          config->pse_pairs_ext)) ||
            (USHRT_MAX !=
                 config->pse_allocated_b && /* Not set for single-signature. */
             NULL ==
                 (to_set = "Power class ext mode A",
                  lldpctl_atom_set_int(dot3_power, lldpctl_k_dot3_power_class_a,
                                       config->power_class_mode_a))) ||
            (USHRT_MAX !=
                 config->pse_allocated_b && /* Not set for single-signature. */
             NULL ==
                 (to_set = "Power class ext mode B",
                  lldpctl_atom_set_int(dot3_power, lldpctl_k_dot3_power_class_b,
                                       config->power_class_mode_b))) ||
            NULL == (to_set = "Power class ext",
                     lldpctl_atom_set_int(dot3_power,
                                          lldpctl_k_dot3_power_class_ext,
                                          config->pd_power_class_ext)) ||
            NULL ==
                (to_set = "Power type ext",
                 lldpctl_atom_set_int(dot3_power, lldpctl_k_dot3_power_type_ext,
                                      config->power_type_ext)) ||
            NULL == (to_set = "PD load (to zero)",
                     lldpctl_atom_set_int(dot3_power,
                                          lldpctl_k_dot3_power_pd_load, 0)) ||
            NULL ==
                (to_set = "PSE maximum available power value",
                 lldpctl_atom_set_int(dot3_power, lldpctl_k_dot3_power_pse_max,
                                      config->pse_max_available_power))) {
            POE_ERR("Failed to set %s for %s", to_set, ifname);
            status = LLDP_POED_ERR_DOT3_POWER_ATOM_FAILED;
            goto fail;
        }
    }

    if (lldpctl_atom_set(port, lldpctl_k_port_dot3_power, dot3_power)) {
        POE_INFO("Successfully transmitted the LLDP Dot3 "
                 "power settings for %s",
                 ifname);
        lldpctl_atom_dec_ref(dot3_power);
    } else {
        POE_ERR("Failed to transmit the LLDP Dot3 "
                "power settings for %s",
                ifname);
        status = LLDP_POED_ERR_DOT3_POWER_ATOM_FAILED;
        goto fail;
    }

    return status;

fail:
    lldpctl_atom_dec_ref(dot3_power);
    return status;
}

/**
 * get_port_atom_by_ifname - Iterate through all interfaces and return the port
 * associated with the given interface name
 * @ifname: network interface name
 * @port: found port lldpctl atom
 *
 * Returns 0 if successful, 1 otherwise.
 */
static int get_port_atom_by_ifname(const char *ifname, lldpctl_atom_t **port)
{
    if (!ifname || !port)
        return LLDP_POED_ERR_INVALID_PARAM;

    lldpctl_atom_t *all_ifaces = lldpctl_get_interfaces(lldpctl_conn);
    if (!all_ifaces) {
        POE_ERR("Failed to retrieve the "
                "interfaces list: %s",
                lldpctl_last_strerror(lldpctl_conn));
        return LLDP_POED_ERR_DOT3_POWER_ATOM_FAILED;
    }

    lldpctl_atom_t *iface_it = NULL;
    int status = LLDP_POED_ERR_DOT3_POWER_ATOM_FAILED;
    lldpctl_atom_foreach(all_ifaces, iface_it)
    {
        if (0 !=
            strcasecmp(lldpctl_atom_get_str(iface_it, lldpctl_k_interface_name),
                       ifname))
            continue;

        /**
         * Matched the port for the given ifname.
         */
        *port = lldpctl_get_port(iface_it);
        lldpctl_atom_dec_ref(iface_it);
        status = LLDP_POED_ERR_OK;
        break;
    }

    lldpctl_atom_dec_ref(all_ifaces);

    return status;
}

/**
 * send_mdi_pse_advertisement - Update the local Dot3 power settings for @ifname
 * and advertise it to its LLDP neighbor
 * @ifname: networking interface name
 * @config: LLDP Dot3 port config
 * @timeout: time at which the advertised LLDPDU expires (nullable)
 *
 * The first call will be treated as the initial PSE MDI advertisement and,
 * hence, will populate the @timeout with the current time plus the local
 * configured TTL (equivalent to the value used by the neighbor to discard the
 * information after it expires). For this to happen, @timeout must be a
 * valid non-null reference.
 * Subsequent calls, where @timeout is set to NULL, will not generate the
 * timeout value again. Every call will determine the Dot3 power configuration
 * to be advertised immediately to the LLDP neighbor.
 *
 * Returns 0, LLDP_POED_ERR_OK, if successful, an error_code otherwise.
 */
int send_mdi_pse_advertisement(const char *ifname,
                               const struct port_dot3_power_settings *config,
                               time_t *timeout)
{
    if (!ifname || !config)
        return LLDP_POED_ERR_INVALID_PARAM;

    int status = LLDP_POED_ERR_OK;
    lldpctl_atom_t *port = NULL;
    if (LLDP_POED_ERR_OK != (status = get_port_atom_by_ifname(ifname, &port))) {
        POE_ERR("Failed to find port with "
                "interface name %s",
                ifname);
        goto done;
    }

    if (LLDP_POED_ERR_OK != (status = write_dot3_power(port, config, ifname))) {
        POE_ERR("Failed to update the Dot3 LLDP "
                "configuration for %s",
                ifname);
        goto done;
    }

    struct port_med_power_settings med_config;
    if (LLDP_POED_ERR_OK != (status = dot3_to_med(config, &med_config))) {
        POE_ERR("Failed to convert Dot3 to the MED LLDP "
                "configuration for %s",
                ifname);
        goto done;
    }

    if (LLDP_POED_ERR_OK != (status = write_med_power(port, &med_config, ifname))) {
        POE_ERR("Failed to update the MED LLDP "
                "configuration for %s",
                ifname);
        goto done;
    }

    if (timeout) {
        /**
         * Populate the timeout as the current time + the local TTL (in
         * seconds).
         */
        time(timeout);
        int local_ttl;
        if (0 != get_local_ttl(&local_ttl)) {
            POE_ERR("Failed to compute "
                    "the local LLDP TTL");
            goto done;
        }
        *timeout += local_ttl;
    }

done:
    lldpctl_atom_dec_ref(port);

    return status;
}

/**
 * is_neighbor_already_reconciled - Check if the only neighbor for the given
 * port has already finished the L2 negotiation.
 * @ifname: networking interface name
 *
 * This function can be used to check if the LLDP neighbor, if exists,
 * already finished the L2 negotiation by having the same value for both
 * "PD requested power value" and "PSE allocated power value".
 *
 * Returns true if already reconciled, false otherwise.
 */
bool is_neighbor_already_reconciled(const char *ifname)
{
    if (!ifname)
        return false;

    lldpctl_atom_t *port = NULL;
    if (0 != get_port_atom_by_ifname(ifname, &port)) {
        POE_ERR("Failed to find port with "
                "interface name %s",
                ifname);
        return false;
    }

    lldpctl_atom_t *neighbors =
        lldpctl_atom_get(port, lldpctl_k_port_neighbors);
    lldpctl_atom_t *neighbor_it = NULL;
    bool status = false;
    lldpctl_atom_foreach(neighbors, neighbor_it)
    {
        lldpctl_atom_t *dot3_power =
            lldpctl_atom_get(neighbor_it, lldpctl_k_port_dot3_power);
        if (!dot3_power) {
            POE_ERR("Unable to retrieve the "
                    "Dot3 power atom for %s: %s",
                    ifname, lldpctl_last_strerror(lldpctl_conn));
            goto done;
        }
        if (lldpctl_atom_get_int(dot3_power, lldpctl_k_dot3_power_devicetype) >
                0 &&
            lldpctl_atom_get_int(dot3_power, lldpctl_k_dot3_power_type) >
                LLDP_DOT3_POWER_8023AT_OFF) {
            status = lldpctl_atom_get_int(dot3_power,
                                          lldpctl_k_dot3_power_requested) ==
                     lldpctl_atom_get_int(dot3_power,
                                          lldpctl_k_dot3_power_allocated);
            lldpctl_atom_dec_ref(dot3_power);
            goto done;
        }
        lldpctl_atom_dec_ref(dot3_power);
        break; /* Neighbor count is limited to 1 during init. */
    }

done:
    lldpctl_atom_dec_ref(neighbor_it);
    lldpctl_atom_dec_ref(neighbors);
    lldpctl_atom_dec_ref(port);
    return status;
}

/**
 * process_neighbor_change - lldpctl watch callback
 *
 * This callback tracks every added/updated/deleted neighbor. For all events
 * we'll push a notification to the port state machine. There is no distinction
 * being made for an added neighbor as compared to an updated one. Remote ports
 * that use any other protocol than standard LLDP will be ignored.
 */
static void process_neighbor_change(lldpctl_conn_t *conn, lldpctl_change_t type,
                                    lldpctl_atom_t *interface,
                                    lldpctl_atom_t *neighbor, void *data)
{
    const char *ifname =
        lldpctl_atom_get_str(interface, lldpctl_k_interface_name);
    int protocol = lldpctl_atom_get_int(neighbor, lldpctl_k_port_protocol);
    if (LLDPD_MODE_LLDP != protocol) {
        for (lldpctl_map_t *protocol_map =
                 lldpctl_key_get_map(lldpctl_k_port_age);
             protocol_map->string; protocol_map++) {
            if (protocol_map->value == protocol) {
                POE_WARN("Unsupported neighbor "
                         "protocol %s for %s",
                         protocol_map->string, ifname);
                return;
            }
        }
    }

    switch (type) {
    case lldpctl_c_added:
    case lldpctl_c_updated: {
        /**
         * TODO: Throttle burst updates.
         */
        struct port_dot3_power_settings dot3_config;
        struct port_med_power_settings med_config;
        int dot3_status;

        if (0 != (dot3_status = read_dot3_power(neighbor, &dot3_config, ifname)))
            POE_WARN("Failed to read Dot3 power "
                     "settings for %s",
                     ifname);

        /**
         * If we receive both dot3 and MED, ignore MED.
         */
        if(0 != dot3_status) {
            if (0 != read_med_power(neighbor, &med_config, ifname)) {
                POE_WARN("Failed to read MED power "
                        "settings for %s",
                        ifname);
            } else {
                med_to_dot3(&med_config, &dot3_config);
            }
        }

        push_lldp_neighbor_update(ifname, &dot3_config);
    } break;
    case lldpctl_c_deleted:
        push_lldp_neighbor_update(ifname, NULL);
        break;
    default:
        POE_WARN("Unknown LLDP change event: %d", type);
        return;
    }
}

/**
 * forward_to_syslog - Call the log with the given severity and message
 */
static void forward_to_syslog(int severity, const char *message)
{
    POE_LOG(severity, "%s", message);
}

/**
 * handle_lldp_events - Receive and send LLDP advertisements
 *
 * On this thread, we'll process neighbor updates in synchronous manner by
 * calling lldpctl_watch() and notifying the port state machine for all neighbor
 * updates. We allow updating the LLDP Dot3 power settings for the initial MDI
 * power support advertisement and for finalizing the L2 negotiation.
 */
void *handle_lldp_events()
{
    /**
     * Redirect all lldpctl logs to syslog.
     */
    lldpctl_log_callback(forward_to_syslog);

    /**
     * Allocate two separate connections with the default synchronous callbacks.
     * One is used for querying the neighbors and the other one for actively
     * watching for changes.
     */
    const char *ctlname = lldpctl_get_default_transport();
    lldpctl_conn = lldpctl_new_name(ctlname, NULL, NULL, NULL);
    lldpctl_conn_t *watch_conn = lldpctl_new_name(ctlname, NULL, NULL, NULL);
    if (!lldpctl_conn || !watch_conn) {
        POE_CRIT("Failed to create an lldpctl connection");
        goto fail;
    }
    /**
     * Check if we have a valid connection with lldpd.
     */
    lldpctl_atom_t *config = lldpctl_get_configuration(watch_conn);
    if (!config) {
        POE_CRIT("Invalid lldpctl connection");
        goto fail;
    }
    if (!lldpctl_atom_set_int(config, lldpctl_k_config_max_neighbors, 1)) {
        POE_CRIT("Failed to limit the maximum number of neighbors: %s",
                 lldpctl_last_strerror(lldpctl_conn));
        lldpctl_atom_dec_ref(config);
        goto fail;
    }

    lldpctl_atom_dec_ref(config);
    /**
     * There is a subtle difference between lldpctl_watch_callback
     * and lldpctl_watch_callback2, but for the sake of using lldpctl
     * 1.0.5, we're going to use lldpctl_watch_callback.
     */
    if (0 !=
        lldpctl_watch_callback(watch_conn, process_neighbor_change, NULL)) {
        POE_CRIT("Failed to register the lldpctl watch callback: %s",
                 lldpctl_last_strerror(lldpctl_conn));
        goto fail;
    }

    POE_WARN("Successfully opened a connection with lldpd. Watching for "
             "changes...");
    while (!thread_exit) {
        if (0 != lldpctl_watch(watch_conn)) {
            POE_CRIT("Unexpected error when watching for neighbor changes: %s",
                     lldpctl_last_strerror(lldpctl_conn));
            goto fail;
        }
    }

    POE_INFO("Exiting handle_lldp_events gracefully");

    return NULL;

fail:
    if (lldpctl_conn)
        lldpctl_release(lldpctl_conn);
    if (watch_conn)
        lldpctl_release(watch_conn);
    return NULL;
}
