/*
 * ngtcp2_wrapper.c - C wrapper to fix amplification limit issue
 * 
 * This directly manipulates ngtcp2's internal dcid.current structure
 * to bypass the amplification limit for local networks.
 */

#include <ngtcp2/ngtcp2.h>
#include <string.h>
#include <arpa/inet.h>
#include <stddef.h>

/* We need to access ngtcp2's internal structures */
#include "../../ngtcp2/lib/ngtcp2_cid.h"
#include "../../ngtcp2/lib/ngtcp2_conn.h"

/**
 * Manually validate the current path for a connection.
 * This bypasses the amplification limit for trusted local networks.
 * 
 * WARNING: Only use for local/private networks (192.168.x.x, 10.x.x.x, 127.x.x.x)
 * 
 * Returns: 0 on success, -1 on error
 */
int ngtcp2_conn_force_validate_path(ngtcp2_conn *conn) {
    if (!conn) {
        return -1;
    }
    
    /* Access dcid.current using the real ngtcp2_conn structure */
    /* The dcid field contains the current destination connection ID */
    
    /* Set path validated flag on dcid.current */
    conn->dcid.current.flags |= NGTCP2_DCID_FLAG_PATH_VALIDATED;
    
    /* Also set bytes_recv to ensure amplification check passes */
    if (conn->dcid.current.bytes_recv == 0) {
        conn->dcid.current.bytes_recv = 3600; /* Pretend we received 3 packets */
    }
    
    return 0;
}

/**
 * Check if a connection's remote address is on a local/private network.
 * Returns: 1 if local, 0 if not
 */
int ngtcp2_path_is_local_network(const ngtcp2_path *path) {
    if (!path || !path->remote.addr) {
        return 0;
    }
    
    struct sockaddr *sa = (struct sockaddr *)path->remote.addr;
    
    if (sa->sa_family == AF_INET) {
        struct sockaddr_in *sin = (struct sockaddr_in *)sa;
        uint32_t addr = ntohl(sin->sin_addr.s_addr);
        
        /* Check for private networks:
         * 192.168.0.0/16: 0xC0A80000 - 0xC0A8FFFF
         * 10.0.0.0/8:     0x0A000000 - 0x0AFFFFFF
         * 127.0.0.0/8:    0x7F000000 - 0x7FFFFFFF
         */
        if ((addr & 0xFFFF0000) == 0xC0A80000 ||  /* 192.168.x.x */
            (addr & 0xFF000000) == 0x0A000000 ||  /* 10.x.x.x */
            (addr & 0xFF000000) == 0x7F000000) {  /* 127.x.x.x */
            return 1;
        }
    }
    
    return 0;
}
