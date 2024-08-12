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

/**
 * This thread establishes netlink socket (AF_NETLINK) connection and monitors
 * network interface up/down events.
 *
 * After binding to socket, the thread subscribes to notifications about changes
 * in network interface (RTMGRP_LINK). Every netlink message has header,
 * followed by a payload with messages being specific to port-id.
 *
 * All messages communications happen over two well known structures nlmsghdr
 * and iovec. Sometimes large messages are split into multiple messages.
 *
 * Upon receiving a network interface down->up event, we will validate if the
 * interface is L1 UP and also RUNNING. we will NOT wait for ARP. We will notify
 * the port state machine for each up and down operational status event.
 *
 * The nlmsghdr and iovec structure are defined as:
 *  struct nlmsghdr {
 *              __u32 nlmsg_len;    // Length of message including header
 *              __u16 nlmsg_type;   // Type of message content
 *              __u16 nlmsg_flags;  // Additional flags
 *              __u32 nlmsg_seq;    // Sequence number
 *              __u32 nlmsg_pid;    // Sender port ID
 *          };
 *
 * struct iovec {
 *              void *iov_base; // data buff
 *              __kernel_size_t iov_len; // size of the data
 *          };
 *
 * Another Important structure is the ifinfomsg, that we need to do deep
 * inspection on to get if_index and state of interface:
 * struct ifinfomsg {
 *               unsigned char        ifi_family;
 *               unsigned char        __ifi_pad;
 *               unsigned short       ifi_type;  // ARPHRD_*
 *               int                  ifi_index; // Link index that
 * will get us port ID and interface name.
 *               unsigned             ifi_flags; // IFF_* flags that we need to
 * check for interface being UP and running
 *               unsigned ifi_change;            //IFF_* change mask
 *         };
 *
 * When aligning netlink messages, after completion of reading them, we will
 * use NLMSG_ALIGN, that is defined in netlink.h:
 * #define NLMSG_ALIGN(len) (((len)+NLMSG_ALIGNTO-1) & ~(NLMSG_ALIGNTO-1))
 *
 * More details about netlink data structures and flags in netlink manpage:
 * https://man7.org/linux/man-pages/man7/netlink.7.html
 */

#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <memory.h>
#include <pthread.h>
#include <regex.h>
#include <signal.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/types.h>
#include <time.h>

#include <linux/rtnetlink.h>
#include <linux/if.h>

#include "cJSON/cJSON.h"
#include "include/common.h"
#include "include/lldp_poed_err.h"
#include "include/logger.h"
#include "include/netlink_event_handler.h"
#include "include/port_state_machine.h"

/**
 * Subscribe mask used for filtering out the netlink events.
 */
#ifndef NETLINK_SUBSCRIBE_GROUP
#define NETLINK_SUBSCRIBE_GROUP RTMGRP_LINK
#endif

/**
 * Must increase this for larger systems or longer thread waits.
 * A lower value may cause truncation on systems
 * with the page size larger than 4096.
 */
#ifndef MESSAGE_BUFFER_MAX_SIZE
#define MESSAGE_BUFFER_MAX_SIZE 8192U
#endif

#ifndef HEARTBEAT_INTERVAL_SEC
#define HEARTBEAT_INTERVAL_SEC 60U
#endif

static const useconds_t netlink_thread_sleep_time = 300000U;

/**
 * setup_netlink_socket_connection - Set up the netlink socket connection
 * @sockfd: socket descriptor to initialize
 * @client_id: client ID structure to initialize
 *
 * Returns whether creating the socket connection and binding to it was
 * successful.
 */
static int setup_netlink_socket_connection(int *sockfd,
                                           struct sockaddr_nl *client_id)
{
    if (!sockfd || !client_id)
        return 1;

    /* Initialize client ID structure. */
    memset(client_id, 0, sizeof(*client_id));
    client_id->nl_family = AF_NETLINK;
    client_id->nl_groups = NETLINK_SUBSCRIBE_GROUP;
    client_id->nl_pid = pthread_self();

    *sockfd = socket(AF_NETLINK, SOCK_RAW, NETLINK_ROUTE);
    if (*sockfd < 0) {
        POE_ERR("Failed to set up the netlink socket: %s", strerror(errno));
        return EXIT_FAILURE;
    }
    POE_DEBUG("Successfully set up the netlink socket");

    if (bind(*sockfd, (struct sockaddr *) client_id, sizeof(*client_id)) < 0) {
        POE_ERR("Failed to bind to netlink socket: %s", strerror(errno));
        close(*sockfd);
        return 1;
    }
    POE_DEBUG("Binding to netlink socket completed.");

    return 0;
}

/**
 * get_ifname_from_interface_info - Get port number as per front panel
 * numbering
 * @interface_info: interface link-level information
 * @current_message_length: netlink message length
 * @ifname: the matched interface name
 *
 * We do not handle if the port is a poe port or not in here. That is
 * up to poed. The interface is identified based on the input structure.
 */
static int get_ifname_from_interface_info(struct ifinfomsg *interface_info,
                                          int current_message_length,
                                          char *ifname)
{
    if (!ifname)
        return 1;

    struct rtattr *attribute_struct = IFLA_RTA(interface_info);
    while (RTA_OK(attribute_struct, current_message_length)) {
        if (attribute_struct->rta_type == IFLA_IFNAME) {
            strncpy(ifname, RTA_DATA(attribute_struct), IFNAMSIZ);
            return 0;
        }
        attribute_struct = RTA_NEXT(attribute_struct, current_message_length);
    }

    *ifname = '\0';
    return 1;
}

/**
 * process_netlink_messages - Detect which interfaces went up or down, based on
 * the ifi_flags field, when receiving a new netlink message
 * @sockfd: caller-initialized socket descriptor
 * @client_id: caller-initialized client ID structure
 *
 * Returns 0 if successful, 1 when there was an error parsing the netlink
 * message.
 */
static int process_netlink_messages(int sockfd, struct sockaddr_nl *client_id)
{
    if (sockfd < 0 || !client_id) {
        POE_ERR("Invalid argument(s)");
        return 1;
    }

    /**
     * Initialize buffers and structures necessary to receive messages over
     * the netlink socket.
     */
    struct nlmsghdr
        message_buffer[MESSAGE_BUFFER_MAX_SIZE / sizeof(struct nlmsghdr)];
    struct iovec iov = {
        .iov_base =
            message_buffer, /* Starting address of socket message buffer */
        .iov_len = sizeof(message_buffer)};

    /* Initialize message header to be used in recvmsg processing. */
    struct msghdr message_header = {.msg_name = client_id,
                                    .msg_namelen = sizeof(*client_id),
                                    .msg_iov = &iov,
                                    .msg_iovlen = 1,
                                    .msg_control = NULL,
                                    .msg_controllen = 0,
                                    .msg_flags = 0};

    /* Receive message in a non-blocking fashion. */
    ssize_t message_len = recvmsg(sockfd, &message_header, MSG_DONTWAIT);
    if (message_len < 0) {
        if (errno == EAGAIN || errno == EINTR) {
            /**
             * Netlink is busy, need to retry.
             */
            return 0;
        }

        POE_ERR("Netlink recv message failed: %s", strerror(errno));
        return 1;
    }

    /**
     * Go through the all messages and notify the port state machine of each
     * link up/down.
     * Map the starting address of buffer to the netlink message
     * header and read until message_len runs out.
     */
    for (struct nlmsghdr *netlink_header = message_buffer;
         NLMSG_OK(netlink_header, message_len);
         NLMSG_NEXT(netlink_header, message_len)) {
        int current_message_length = netlink_header->nlmsg_len;
        POE_DEBUG("message_len: %lu", message_len);
        char ifname[IFNAMSIZ];
        struct ifinfomsg *interface_info = NLMSG_DATA(netlink_header);
        const int err = get_ifname_from_interface_info(
            interface_info, current_message_length, ifname);
        if (0 != err) {
            POE_ERR("Failed to get ifname from interface RTA structure: %s",
                    strerror(errno));
            return 1;
        }

        /**
         * TODO: Coalesce multiple events coming for the same port and send the
         * link update in batches to the state machine.
         */
        bool l1_up = interface_info->ifi_flags & IFF_LOWER_UP;
        bool l2_up = interface_info->ifi_flags & IFF_RUNNING;
        bool admin_up = interface_info->ifi_flags & IFF_UP;
        if (l2_up) {
            POE_INFO("Interface is operationally up (RUNNING). Name: %s, "
                     "ifi_index: %d",
                     ifname, interface_info->ifi_index);
            push_if_link_update(ifname, PORT_IF_UP);
        } else if (admin_up) {
            POE_INFO("Interface %d is set to admin UP, but has no active L2 "
                     "link. Carrier L1 (LOWER_UP) status is %s",
                     interface_info->ifi_index, (l1_up) ? "UP" : "DOWN");
            push_if_link_update(ifname, PORT_IF_DOWN);
        } else {
            POE_INFO("Interface was set to admin DOWN. Name: %s, ifi_index: %d",
                     ifname, interface_info->ifi_index);
            push_if_link_update(ifname, PORT_IF_DOWN);
        }

        int current_message_type = netlink_header->nlmsg_type;
        POE_DEBUG("Received netlink message of type: %d", current_message_type);
    }

    return 0;
}

/**
 * handle_netlink_events - Process relevant netlink messages
 *
 * Detect all ports that come up or down and advertise their state to the port
 * state machine.
 */
void *handle_netlink_events()
{
    int sockfd;
    struct sockaddr_nl client_id;
    int status = setup_netlink_socket_connection(&sockfd, &client_id);
    if (status != 0) {
        POE_ERR("Failed to setup netlink socket, "
                "Exiting...");
        return NULL;
    }
    POE_NOTICE("Successfully completed netlink socket communication");

    /**
     * Process netlink events from RTMGRP_LINK group.
     * We are going to recvmsg on socket and process them one at a
     * time.
     */
    time_t current_system_time = time(NULL);
    struct tm *heartbeat_time =
        gmtime(&current_system_time); /* This will ensure portability. */
    while (!thread_exit) {
        status = process_netlink_messages(sockfd, &client_id);
        if (time(NULL) > mktime(heartbeat_time)) {
            publish_metrics("lldp_poed_heartbeat", 1, 0);
            heartbeat_time->tm_min += HEARTBEAT_INTERVAL_SEC / 60;
        }

        usleep(netlink_thread_sleep_time);
    }

    POE_NOTICE("Closing netlink socket and exiting "
             "handle_netlink_events gracefully");
    close(sockfd);

    return NULL;
}

/**
 * is_link_state_up_and_running - Determine if the given interface is up and
 * running
 * @ifname: the interface name
 *
 * Returns true if IFF_UP and IFF_RUNNING flags are present for the given
 * interface.
 */
static bool is_link_state_up_and_running(const char *ifname)
{
    int sockfd = socket(AF_INET, SOCK_DGRAM, IPPROTO_IP);
    if (sockfd < 0) {
        POE_ERR("Socket failed. Errno = %s\n", strerror(errno));
        return false;
    }

    struct ifreq if_req;
    strncpy(if_req.ifr_name, ifname, sizeof(if_req.ifr_name));
    const int ret = ioctl(sockfd, SIOCGIFFLAGS, &if_req);
    close(sockfd);

    if (-1 == ret) {
        POE_ERR("Ioctl failed. Errno = %s\n", strerror(errno));
        return false;
    }
    POE_DEBUG("Port state flag: 0x%x. Name: %s", if_req.ifr_flags, ifname);

    if ((if_req.ifr_flags & IFF_UP) && (if_req.ifr_flags & IFF_RUNNING))
        return true;
    return false;
}

/**
 * scan_all_ports - Determine which ports are already up and running
 * @pr: port range used for scanning
 *
 * The detected ports will be reported as operationally up to the port state
 * machine.
 *
 * Returns 0 if successful, 1 otherwise.
 */
int netlink_scan_all_ports(struct port_range *pr)
{
    if (!pr)
        return 1;

    FOR_I_IN(pr->start_index, pr->end_index)
    {
        char port_name[IFNAMSIZ];
        snprintf(port_name, IFNAMSIZ, "%s%ld", pr->ifname_prefix, i);
        POE_INFO("Scanning port: %s", port_name);
        if (is_link_state_up_and_running(port_name)) {
            POE_INFO("Port %s is up and running", port_name);
            push_if_link_update(port_name, PORT_IF_UP);
        }
    }

    return 0;
}
