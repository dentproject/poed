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

#include <pthread.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "include/common.h"
#include "include/lldp_poed_err.h"
#include "include/logger.h"
#include "include/queue.h"

/**
 * free_linked_list - Free all linked list nodes
 * @head: the head node of the list
 */
void free_linked_list(struct linked_list *head)
{
    while (head) {
        struct linked_list *temp = head;
        head = head->next;
        free(temp);
    }
}

/**
 * insert_after - Create a new node and insert it after the input node
 * @current: existing node after which to insert
 *
 * Returns the newly added node.
 */
struct linked_list *insert_after(struct linked_list *current)
{
    if (!current)
        return NULL;

    struct linked_list *new_node =
        (struct linked_list *) malloc(sizeof(struct linked_list));
    new_node->next = NULL;
    current->next = new_node;

    return new_node;
}

/**
 * q_init - Initialize the queue data structure
 * @q: input queue reference
 * @use_lock: reentrant flag
 */
void q_init(struct queue *q, bool use_lock)
{
    if (!q)
        return;

    q->head = NULL;
    q->tail = NULL;
    q->use_lock = use_lock;
    if (q->use_lock) {
        pthread_mutex_init(&q->q_mutex, NULL);
    }
}

/**
 * q_enqueue - Enqueue a new node into the given queue
 * @q: caller-initialized queue
 * @node: new node to insert
 *
 * This function will copy the input node to the queue, not reference it.
 */
void q_enqueue(struct queue *q, struct linked_list *node)
{
    if (!q || !node)
        return;

    struct linked_list *new_node =
        (struct linked_list *) malloc(sizeof(struct linked_list));
    memcpy(new_node, node, sizeof(struct linked_list));
    new_node->next = NULL;
    Q_LOCK(q);
    if (q->head == NULL) {
        Q_UNLOCK(q);
        q->head = q->tail = new_node;
        return;
    }
    /**
     * Otherwise, update the tail only.
     */
    q->tail->next = new_node;
    q->tail = new_node;
    Q_UNLOCK(q);
}

/**
 * q_dequeue - Dequeue a list from the queue
 * @q: caller-initialized queue
 *
 * @warning: caller has the responsibility to free the returned list node.
 */
struct linked_list *q_dequeue(struct queue *q)
{
    if (!q)
        return NULL;

    struct linked_list *front;
    Q_LOCK(q);
    if (q->head == NULL) {
        Q_UNLOCK(q);
        return NULL;
    }
    front = q->head;
    q->head = q->head->next;
    if (!q->head) {
        POE_DEBUG("Port queue is now empty");
    }
    Q_UNLOCK(q);

    return front;
}

/**
 * @q_destroy - Free all queue nodes.
 * @q: caller-initialized queue
 *
 * @warning: caller has to free up the node member's dynamic memory (if any).
 */
void q_destroy(struct queue *q)
{
    if (!q)
        return;

    struct linked_list *node_it;
    Q_LOCK(q);
    while (q->head) {
        node_it = q_dequeue(q);
        free(node_it->value);
        free(node_it);
    }
    Q_UNLOCK(q);
}
