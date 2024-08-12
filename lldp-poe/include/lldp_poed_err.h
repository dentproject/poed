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


#ifndef _LLDP_POE_LLDP_POED_ERR_H_
#define _LLDP_POE_LLDP_POED_ERR_H_

/**
 * lldp_poed_err - Function status codes
 * @LLDP_POED_ERR_OK: successful
 * @LLDP_POED_ERR_INVALID_PARAM: invalid argument supplied to the function
 * @LLDP_POED_ERR_GETPORTDETAILS_FAILED: poed query for get_port_details failed
 * @LLDP_POED_ERR_SEND_REQUEST_FAILED: failed to send request to poed
 * @LLDP_POED_ERR_UNEXPECTED_DELETED_NEIGHBOR: LLDP neighbor got deleted in the
 * meantime (unexpectedly)
 * @LLDP_POED_ERR_DELETED_NEIGHBOR: LLDP neighbor got deleted or aged out. Port
 * will go back to L1 neg complete
 * @LLDP_POED_INVALID_PAYLOAD: malformed payload
 * @LLDP_POED_ERR_PORT_GOT_DISABLED: PoE port got disabled in the meantime
 * @LLDP_POED_ERR_PORT_ERROR: PoE port is in error state
 * @LLDP_POED_ERR_PORT_ERROR: PoE port got the default power limit
 * @LLDP_POED_ERR_8023AT_FIELDS_MISSING: 802.3at fields were not supplied
 * @LLDP_POED_ERR_INVALID_8023AT_FIELDS: 802.3at fields are invalid
 * @LLDP_POED_8023BT_FIELDS_ERROR: 802.3bt fields are invalid
 * @LLDP_POED_ERR_SERIALIZE_ERROR: failed to serialize the payload
 * @LLDP_POED_ERR_PARSE_ERROR: failed to parse the payload
 * @LLDP_POED_ERR_LLDP_PROCESSING_DISABLED: LLDP is disabled for the port
 * @LLDP_POED_ERR_INACTIVE_DATALINK: there is no active data link for the port
 * @LLDP_POED_ERR_UNEXPECTED_DEVICE_TYPE: invalid power device detected
 * @LLDP_POED_ERR_FAILED_TO_SET_L2_TPPL: poed failed to apply the port TPPL
 * @LLDP_POED_ERR_DUALSIG_PD_NOT_SUPPORTED: dual-signature PDs are not supported
 * @LLDP_POED_ERR_DOT3_POWER_ATOM_FAILED: failed to set the lldpctl atom
 * @LLDP_POED_ERR_MED_POWER_ATOM_FAILED: failed to set the lldpctl MED atom
 * @LLDP_POED_ERR_PIPE_ISSUE: named pipe error
 * @LLDP_POED_ERR_INTERNAL_ERROR: generic internal error
 */
enum lldp_poed_err {
    LLDP_POED_ERR_OK = 0,
    LLDP_POED_ERR_INVALID_PARAM,
    LLDP_POED_ERR_GETPORTDETAILS_FAILED,
    LLDP_POED_ERR_SEND_REQUEST_FAILED,
    LLDP_POED_ERR_UNEXPECTED_DELETED_NEIGHBOR,
    LLDP_POED_ERR_DELETED_NEIGHBOR,
    LLDP_POED_ERR_INVALID_PAYLOAD,
    LLDP_POED_ERR_PORT_GOT_DISABLED,
    LLDP_POED_ERR_PORT_ERROR,
    LLDP_POED_ERR_PORT_DEFAULT_POWER,
    LLDP_POED_ERR_8023AT_FIELDS_MISSING,
    LLDP_POED_ERR_INVALID_8023AT_FIELDS,
    LLDP_POED_ERR_INVALID_8023BT_FIELDS,
    LLDP_POED_ERR_SERIALIZE_ERROR,
    LLDP_POED_ERR_PARSE_ERROR,
    LLDP_POED_ERR_LLDP_PROCESSING_DISABLED,
    LLDP_POED_ERR_INACTIVE_DATALINK,
    LLDP_POED_ERR_UNEXPECTED_DEVICE_TYPE,
    LLDP_POED_ERR_FAILED_TO_SET_L2_TPPL,
    LLDP_POED_ERR_DUALSIG_PD_NOT_SUPPORTED,
    LLDP_POED_ERR_DOT3_POWER_ATOM_FAILED,
    LLDP_POED_ERR_MED_POWER_ATOM_FAILED,
    LLDP_POED_ERR_PIPE_ISSUE,
    LLDP_POED_ERR_INTERNAL_ERROR,
};

#endif /* _LLDP_POE_LLDP_POED_ERR_H_ */
