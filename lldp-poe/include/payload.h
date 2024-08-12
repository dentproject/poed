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

#ifndef _LLDP_POE_PAYLOAD_H_
#define _LLDP_POE_PAYLOAD_H_

/**
 * Payload abstraction layer used for translating a port state
 * machine message to the underlying messaging protocol. The payload represents
 * a tree-like structure containing communication data exchanged between
 * poed and lldp-poed.
 * Ideally, we should have a common API for translating to and from
 * the underlying protocol and the init API should set the desired protocol
 * to be used by the translate API.
 * This is usually achieved via dependency injection and polymorphism by
 * having several different implementations that use the same
 * interface/contract, but since we are in C and we have only one protocol
 * at the moment, we'll couple to just one implementation treating JSON-RPC.
 */

#include <stdbool.h>
#include <stddef.h>

/**
 * enum payload_value_type - Possible payload value type
 * @PAYLOAD_VALUE_BOOLEAN: bool data type (true/false)
 * @PAYLOAD_VALUE_NUMBER: only ints
 * @PAYLOAD_VALUE_STRING: string (char[])
 * @PAYLOAD_VALUE_ARRAY: array of payload objects
 * @PAYLOAD_VALUE_OBJECT: nested object
 * @PAYLOAD_VALUE_NULL: null
 * @PAYLOAD_VALUE_MAX: total number of types
 */
enum payload_value_type {
    PAYLOAD_VALUE_BOOLEAN,
    PAYLOAD_VALUE_NUMBER,
    PAYLOAD_VALUE_STRING,
    PAYLOAD_VALUE_ARRAY,
    PAYLOAD_VALUE_OBJECT,
    PAYLOAD_VALUE_NULL,
    PAYLOAD_VALUE_MAX,
};

/**
 * Payload boundaries.
 */
#define PAYLOAD_NAME_MAX_SIZE 32U
#define PAYLOAD_VAL_STR_MAX_SIZE 32U

/**
 * Currently supported data types are: int, bool and string.
 */
union object_value {
    int val_int;
    bool val_bool;
    char val_str[PAYLOAD_VAL_STR_MAX_SIZE];
};

/**
 * struct poed_payload - Payload model covering all possible use-cases
 * for making requests and receiving replies from poed
 * @name: key name. Array children names are ignored
 * @type: the type of the object (see @payload_value_type)
 * @value: object value (this field must not be used with ARRAY, OBJECT or
 * NULL)
 * @child_count: number of child objects (valid only for ARRAY and OBJECT)
 * @children: child nodes (each node value can be accessed through @value)
 *
 * Note that this container is agnostic of the data format used for
 * serializing/deserializing the final message.
 */
struct poed_payload {
    char name[PAYLOAD_NAME_MAX_SIZE];
    enum payload_value_type type;
    union object_value value;
    size_t child_count;
    struct poed_payload *children;
};

int find_payload_by_key(const struct poed_payload *, const char *,
                        const struct poed_payload **);

int payload_to_json_rpc(const struct poed_payload *, const char *, ssize_t *,
                        char *, size_t);

int json_rpc_to_payload(const char *, size_t, const ssize_t,
                        struct poed_payload *);

void log_payload(const struct poed_payload *);

void free_payload(struct poed_payload *);

#endif /* _LLDP_POE_PAYLOAD_H_ */
