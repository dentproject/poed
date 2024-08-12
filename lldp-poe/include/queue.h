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

#ifndef _LLDP_POE_QUEUE_H_
#define _LLDP_POE_QUEUE_H_

#include <pthread.h>
#include <stdbool.h>

/**
 * Shortcut macros for performing mutex lock/unlock.
 */
#define Q_LOCK(q)                                                              \
    ({                                                                         \
        if (q->use_lock)                                                       \
            pthread_mutex_lock(&q->q_mutex);                                   \
    })
#define Q_UNLOCK(q)                                                            \
    ({                                                                         \
        if (q->use_lock)                                                       \
            pthread_mutex_unlock(&q->q_mutex);                                 \
    })

/**
 * struct linked_list - Singly linked list
 * @value: generic value
 * @next: next node in the list
 *
 * TODO: Provide dealloc hook for the caller.
 * Note: @value must not have dynamically-allocated fields.
 */
struct linked_list {
    void *value;
    struct linked_list *next;
};

/**
 * struct queue - Reentrant queue structure, using a singly linked list as the
 * underlying data structure.
 * @head: front of the queue
 * @tail: back of the queue
 * @q_mutex: mutex object to use for synchronizing access
 * @use_lock: sync flag
 */
struct queue {
    struct linked_list *head;
    struct linked_list *tail;
    pthread_mutex_t q_mutex;
    bool use_lock;
};

struct linked_list *insert_after(struct linked_list *);

void free_linked_list(struct linked_list *);

void q_init(struct queue *, bool);

void q_enqueue(struct queue *, struct linked_list *);

struct linked_list *q_dequeue(struct queue *);

void q_destroy(struct queue *);

#endif /* _LLDP_POE_QUEUE_H_ */