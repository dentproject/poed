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

#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "cJSON/cJSON.h"
#include "include/common.h"
#include "include/lldp_poed_err.h"
#include "include/logger.h"
#include "include/payload.h"

#ifndef JSON_RPC_VER
#define JSON_RPC_VER "2.0"
#endif

/**
 * find_payload_by_key - Find the payload node by key and return its value
 * @payload: payload to be searched
 * @key_name: key name
 * @val: value to be referenced, if found
 *
 * The search is done recursively across all children. @val is used
 * as struct poed_payload to cover also compound objects (array and
 * object).
 *
 * Returns 0 if the key was found, 1 otherwise.
 */
int find_payload_by_key(const struct poed_payload *payload,
                        const char *key_name, const struct poed_payload **val)
{
    if (!payload || !key_name || !val || payload->type >= PAYLOAD_VALUE_MAX)
        return 1;

    int status = 1;
    if (0 == strncmp(key_name, payload->name, PAYLOAD_NAME_MAX_SIZE)) {
        *val = payload;
        status = 0;
    } else if (PAYLOAD_VALUE_OBJECT == payload->type ||
               PAYLOAD_VALUE_ARRAY == payload->type) {
        const struct poed_payload *payload_it = NULL;
        FOR_EACH(payload_it, payload->children, payload->child_count)
        {
            status = find_payload_by_key(payload_it, key_name, val);
            if (0 == status)
                break;
        }
    }

    return status;
}

/**
 * add_all_payload_children - Recurse into all @payload children and
 * add all fields to the @root node.
 * @root: cJSON root node
 * @children: children to translate to cJSON
 * @type: root payload type
 * @count: children count
 *
 * @warning: caller has the responsibility to free the @root
 *
 * @children must be an array of valid types, otherwise it will return 1.
 *
 * Returns 0 if successful, 1 otherwise.
 */
static int add_all_payload_children(struct cJSON *root,
                                    const struct poed_payload *children,
                                    enum payload_value_type type, size_t count)
{
    if (!root || !children || !count || type >= PAYLOAD_VALUE_MAX ||
        (!cJSON_IsObject(root) && !cJSON_IsArray(root)))
        return 1;

    const struct poed_payload *children_it = NULL;
    FOR_EACH(children_it, children, count)
    {
        cJSON *child_node = NULL;
        switch (children_it->type) {
        case PAYLOAD_VALUE_BOOLEAN:
            child_node = cJSON_CreateBool(children_it->value.val_bool);
            break;
        case PAYLOAD_VALUE_NULL:
            child_node = cJSON_CreateNull();
            break;
        case PAYLOAD_VALUE_NUMBER:
            child_node = cJSON_CreateNumber(children_it->value.val_int);
            break;
        case PAYLOAD_VALUE_STRING:
            child_node = cJSON_CreateString(children_it->value.val_str);
            break;
        case PAYLOAD_VALUE_OBJECT:
            child_node = cJSON_CreateObject();
            break;
        case PAYLOAD_VALUE_ARRAY:
            child_node = cJSON_CreateArray();
            break;
        default:
            POE_ERR("Unknown payload type: %d", children_it->type);
            return 1;
        }
        if (NULL == child_node)
            return 1;

        /**
         * Recurse for objects and arrays before appending the child to the
         * root. In case it's a primitive type, just append it to the root.
         */
        if (PAYLOAD_VALUE_OBJECT == children_it->type ||
            PAYLOAD_VALUE_ARRAY == children_it->type) {

            if (0 != add_all_payload_children(child_node, children_it->children,
                                              children_it->type,
                                              children_it->child_count))
                return 1;

            if (PAYLOAD_VALUE_ARRAY == children_it->type)
                cJSON_AddItemToArray(root, child_node);
            else if (PAYLOAD_VALUE_OBJECT == children_it->type) {
                if ('\0' == children_it->name[0]) {
                    POE_ERR("JSON key name cannot be empty for an object");
                    return 1;
                }
            }
        }

        cJSON_AddItemToObject(root, children_it->name, child_node);
    }

    return 0;
}

/**
 * payload_to_json_rpc - Serialize the poed payload to a JSON-RPC message
 * @payload: payload to be serialized
 * @method: value for the "method" JSON field
 * @id: generated request ID
 * @json: buffer for the final JSON string
 * @max_size: pre-allocated @json buffer size
 *
 * Will populate the "params" field with the payload and also "id" and "method"
 * fields. If @payload is empty, then this field will be omitted from the final
 * request.
 *
 * Returns 0 if successful, 1 otherwise.
 */
int payload_to_json_rpc(const struct poed_payload *payload, const char *method,
                        ssize_t *id, char *json, size_t max_size)
{
    static ssize_t request_id_counter = 0;

    if (!payload || !method || !id || !json)
        return 1;

    cJSON *message = cJSON_CreateObject();
    if (NULL == message)
        goto fail;
    if (NULL == cJSON_AddStringToObject(message, "jsonrpc", JSON_RPC_VER))
        goto fail;
    if (NULL == cJSON_AddStringToObject(message, "method", method))
        goto fail;

    switch (payload->type) {
    case PAYLOAD_VALUE_OBJECT:
        if (0 == payload->child_count) {
            POE_DEBUG("Skipping 'params' field for %s", method);
            break;
        }

        /* This means we have children to add as params to the request. */
        cJSON *params = NULL;
        params = cJSON_AddObjectToObject(message, "params");
        if (0 != add_all_payload_children(params, payload->children,
                                          payload->type, payload->child_count))
            goto fail;

        break;
    default:
        POE_ERR("Invalid payload type ('params' must be an object)");
        goto fail;
        break;
    }

    *id = ++request_id_counter;
    cJSON *req_id = cJSON_CreateNumber(request_id_counter);
    if (NULL == req_id)
        goto fail;
    if (false == cJSON_AddItemToObject(message, "id", req_id))
        goto fail;

    if (false == cJSON_PrintPreallocated(message, json, max_size, false))
        goto fail;
    cJSON_Delete(message);

    return 0;

fail:
    POE_ERR("Failed to construct JSON-RPC message for %s", method);
    cJSON_Delete(message);
    *id = -1;
    return 1;
}

/**
 * add_all_cjson_children - Recurse into all @cjson children and add
 * all fields to the @root node.
 * @root: payload root node
 * @cjson: cJSON to traverse
 *
 * @warning: caller has the responsibility to free the @root
 *
 * Returns 0 if successful, 1 otherwise.
 */
static int add_all_cjson_children(struct poed_payload *root,
                                  const struct cJSON *cjson)
{
    if (!root || !cjson)
        return 1;

    root->child_count = 0;
    root->children = NULL;
    if (cJSON_IsNumber(cjson)) {
        root->type = PAYLOAD_VALUE_NUMBER;
        root->value.val_int = cjson->valueint;
    } else if (cJSON_IsString(cjson)) {
        root->type = PAYLOAD_VALUE_STRING;
        strncpy(root->value.val_str, cjson->valuestring,
                PAYLOAD_VAL_STR_MAX_SIZE);
    } else if (cJSON_IsBool(cjson)) {
        root->type = PAYLOAD_VALUE_BOOLEAN;
        root->value.val_int = (cjson->valueint ? true : false);
    } else if (cJSON_IsNull(cjson)) {
        root->type = PAYLOAD_VALUE_NULL;
        memset(&(root->value), 0, sizeof(root->value));
    } else if (cJSON_IsObject(cjson) || cJSON_IsArray(cjson)) {
        root->type =
            cJSON_IsObject(cjson) ? PAYLOAD_VALUE_OBJECT : PAYLOAD_VALUE_ARRAY;
        /* The cJSON API is misleading here. Array can also mean Object... */
        root->child_count = cJSON_GetArraySize(cjson);
        root->children = calloc(root->child_count, sizeof(struct poed_payload));
        int child_idx = 0;
        const cJSON *child_it = NULL;
        cJSON_ArrayForEach(child_it, cjson)
        {
            if (cJSON_IsObject(cjson))
                strncpy(root->children[child_idx].name, child_it->string,
                        PAYLOAD_NAME_MAX_SIZE);
            if (0 !=
                add_all_cjson_children(&(root->children[child_idx]), child_it))
                return 1;
            child_idx++;
        }
    } else {
        POE_ERR("Unknown cJSON type: %d", cjson->type);
        return 1;
    }

    return 0;
}

/**
 * json_rpc_to_payload - Deserialize a JSON-RPC message to poed payload
 * @json: input buffer
 * @max_size: pre-allocated @json buffer size
 * @id: id to match in the message
 * @payload: payload to deserialize to
 *
 * @warning: caller has the responsibility to free the @payload
 *
 * Returns 0, LLDP_POED_ERR_OK, if successful, an error_code otherwise.
 *
 * Note: if the response is actually an error, this will return 1 and log
 * the error. An error is generated also if the message ID doesn't match the
 * input @id.
 */
int json_rpc_to_payload(const char *json, size_t max_size, const ssize_t id,
                        struct poed_payload *payload)
{
    if (!json || !payload)
        return LLDP_POED_ERR_INVALID_PARAM;

    int status = LLDP_POED_ERR_OK;
    cJSON *message = cJSON_ParseWithLength(json, max_size);
    if (NULL == message) {
        status = LLDP_POED_ERR_PARSE_ERROR;
        goto fail;
    }
    cJSON *json_rpc_version =
        cJSON_GetObjectItemCaseSensitive(message, "jsonrpc");
    if (!cJSON_IsString(json_rpc_version) || !json_rpc_version->valuestring ||
        0 != strncmp(json_rpc_version->valuestring, "2.0", 4)) {
        status = LLDP_POED_ERR_PARSE_ERROR;
        goto fail;
    }
    cJSON *res_id = cJSON_GetObjectItemCaseSensitive(message, "id");
    if (!cJSON_IsNumber(res_id) || res_id->valueint != id) {
        status = LLDP_POED_ERR_PARSE_ERROR;
        goto fail;
    }

    if (cJSON_HasObjectItem(message, "error")) {
        cJSON *error = cJSON_GetObjectItemCaseSensitive(message, "error");
        cJSON *message = cJSON_GetObjectItemCaseSensitive(error, "message");
        if (message->valuestring) {
            POE_ERR("JSON-RPC response error message: %s",
                    message->valuestring);
        } else {
            POE_ERR(
                "Unknown JSON-RPC response error (missing 'message' field)");
            status = LLDP_POED_ERR_PARSE_ERROR;
            goto fail;
        }
    }

    if (cJSON_HasObjectItem(message, "result")) {
        cJSON *result = cJSON_GetObjectItemCaseSensitive(message, "result");
        if (cJSON_IsNumber(result) || cJSON_IsString(result) ||
            cJSON_IsObject(result)) {
            strncpy(payload->name, "result", PAYLOAD_NAME_MAX_SIZE);
            if (0 != add_all_cjson_children(payload, result)) {
                status = LLDP_POED_ERR_INTERNAL_ERROR;
                goto fail;
            }
        } else {
            POE_ERR("Invalid cJSON 'result' type: %d", result->type);
            status = LLDP_POED_ERR_PARSE_ERROR;
            goto fail;
        }
    } else {
        POE_ERR("Missing 'result' field in the JSON-RPC response");
        status = LLDP_POED_ERR_PARSE_ERROR;
        goto fail;
    }

    cJSON_Delete(message);

    return status;

fail:; /* C89 compliance for labels (labels must always start with a statement)
        */
    const char *err = cJSON_GetErrorPtr();
    POE_ERR("Failed to parse JSON-RPC message: %s", (err) ? err : "");
    cJSON_Delete(message);
    return status;
}

/**
 * log_payload - Log all payload contents for debug purposes
 * @payload: caller-initialized payload
 */
void log_payload(const struct poed_payload *payload)
{
    if (!payload)
        return;

    if (PAYLOAD_VALUE_OBJECT == payload->type ||
        PAYLOAD_VALUE_ARRAY == payload->type) {
        POE_DEBUG("Payload array/object name: %s",
                  (strlen(payload->name)) ? payload->name : "None");
        struct poed_payload *payload_it = NULL;
        FOR_EACH(payload_it, payload->children, payload->child_count)
        {
            POE_DEBUG("------------------------------------------------"
                      "--------------");
            log_payload(payload_it);
        }
    } else if (PAYLOAD_VALUE_BOOLEAN == payload->type) {
        POE_DEBUG("Payload type: boolean, name: %s, value: %s",
                  (strlen(payload->name)) ? payload->name : "NULL",
                  (payload->value.val_bool ? "true" : "false"));
    } else if (PAYLOAD_VALUE_NUMBER == payload->type) {
        POE_DEBUG("Payload type: number, name: %s, value: %d",
                  (strlen(payload->name)) ? payload->name : "NULL",
                  payload->value.val_int);
    } else if (PAYLOAD_VALUE_STRING == payload->type) {
        POE_DEBUG("Payload type: string, name: %s, value: %s",
                  (strlen(payload->name)) ? payload->name : "NULL",
                  payload->value.val_str);
    } else if (PAYLOAD_VALUE_NULL == payload->type) {
        POE_DEBUG("Payload type: null, name: %s",
                  (strlen(payload->name)) ? payload->name : "NULL");
    } else {
        POE_DEBUG("Unknown payload type: %d, name: %s", payload->type,
                  (strlen(payload->name)) ? payload->name : "NULL");
    }
}

/**
 * free_payload - Free all children dynamically-allocated memory.
 * @payload: caller-initialized payload
 *
 * Once the payload has been freed up, it can be safely reused.
 */
void free_payload(struct poed_payload *payload)
{
    if (!payload)
        return;

    if (PAYLOAD_VALUE_OBJECT == payload->type ||
        PAYLOAD_VALUE_ARRAY == payload->type) {
        if (!payload->children)
            return;

        struct poed_payload *payload_it = NULL;
        FOR_EACH(payload_it, payload->children, payload->child_count)
        {
            free_payload(payload_it);
        }
        free(payload->children);
        payload->children = NULL;
    }
}
