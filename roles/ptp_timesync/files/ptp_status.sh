#!/bin/bash
# Polls the running ptp4l socket via pmc and writes a snapshot of
# PARENT_DATA_SET, PORT_DATA_SET, and TIME_STATUS_NP into PTP_STATUS_FILE
# once a second. Designed to be served into relay VMs via virtiofs so an
# in-guest watchdog can read host PTP state without owning a NIC.
#
# Required env (set by the systemd unit):
#   PTP4L_SOCKET_PATH   e.g. /var/run/timemaster/ptp4l.<num>.socket
#   PTP_STATUS_FOLDER   e.g. /home/libvirt-local/ptp

set -u

: "${PTP4L_SOCKET_PATH:?PTP4L_SOCKET_PATH must be set}"
: "${PTP_STATUS_FOLDER:?PTP_STATUS_FOLDER must be set}"

PTP_STATUS_FILE="${PTP_STATUS_FOLDER}/ptp_status"
test -d "${PTP_STATUS_FOLDER}" || mkdir -p "${PTP_STATUS_FOLDER}"

while true
do
    # Default to socket index 0; if chrony reports a selected (*) PHC
    # source on a different ptp4l instance, switch to its socket. This
    # handles multi-NIC PTP setups where ptp4l runs once per interface.
    socket="${PTP4L_SOCKET_PATH//<num>/0}"
    while read -r line
    do
        master="${line:2:1}"
        if [ "${master}" = "*" ]; then
            ptpnum=$(echo "${line}" | cut -d',' -f3 -)
            ptpnum="${ptpnum:3:1}"
            socket="${PTP4L_SOCKET_PATH//<num>/${ptpnum}}"
            break
        fi
    done < <(chronyc -c sources 2>/dev/null || true)

    if [ -S "${socket}" ]; then
        pmc -s "${socket}" -b 0 -u \
            "GET TIME_STATUS_NP" \
            "GET PORT_DATA_SET" \
            "GET PARENT_DATA_SET" \
            > "${PTP_STATUS_FILE}.tmp" 2>/dev/null \
            && mv "${PTP_STATUS_FILE}.tmp" "${PTP_STATUS_FILE}"
    else
        printf 'ptp_status: socket %s not present yet\n' "${socket}" \
            > "${PTP_STATUS_FILE}.tmp"
        mv "${PTP_STATUS_FILE}.tmp" "${PTP_STATUS_FILE}"
    fi

    sleep 1
done
