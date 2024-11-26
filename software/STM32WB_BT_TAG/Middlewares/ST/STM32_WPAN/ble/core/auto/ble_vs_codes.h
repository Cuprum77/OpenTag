/*****************************************************************************
 * @file    ble_vs_codes.h
 * @brief   STM32WB BLE API (vendor specific event codes)
 *          Auto-generated file: do not edit!
 *****************************************************************************
 * @attention
 *
 * Copyright (c) 2018-2024 STMicroelectronics.
 * All rights reserved.
 *
 * This software is licensed under terms that can be found in the LICENSE file
 * in the root directory of this software component.
 * If no LICENSE file comes with this software, it is provided AS-IS.
 *
 *****************************************************************************
 */

#ifndef BLE_VS_CODES_H__
#define BLE_VS_CODES_H__


/* Vendor specific codes of ACI GAP events
 */

/* ACI_GAP_LIMITED_DISCOVERABLE_EVENT code */
#define ACI_GAP_LIMITED_DISCOVERABLE_VSEVT_CODE           0x0400U

/* ACI_GAP_PAIRING_COMPLETE_EVENT code */
#define ACI_GAP_PAIRING_COMPLETE_VSEVT_CODE               0x0401U

/* ACI_GAP_PASS_KEY_REQ_EVENT code */
#define ACI_GAP_PASS_KEY_REQ_VSEVT_CODE                   0x0402U

/* ACI_GAP_AUTHORIZATION_REQ_EVENT code */
#define ACI_GAP_AUTHORIZATION_REQ_VSEVT_CODE              0x0403U

/* ACI_GAP_PERIPHERAL_SECURITY_INITIATED_EVENT code */
#define ACI_GAP_PERIPHERAL_SECURITY_INITIATED_VSEVT_CODE  0x0404U

/* ACI_GAP_BOND_LOST_EVENT code */
#define ACI_GAP_BOND_LOST_VSEVT_CODE                      0x0405U

/* ACI_GAP_PROC_COMPLETE_EVENT code */
#define ACI_GAP_PROC_COMPLETE_VSEVT_CODE                  0x0407U

/* ACI_GAP_ADDR_NOT_RESOLVED_EVENT code */
#define ACI_GAP_ADDR_NOT_RESOLVED_VSEVT_CODE              0x0408U

/* ACI_GAP_NUMERIC_COMPARISON_VALUE_EVENT code */
#define ACI_GAP_NUMERIC_COMPARISON_VALUE_VSEVT_CODE       0x0409U

/* ACI_GAP_KEYPRESS_NOTIFICATION_EVENT code */
#define ACI_GAP_KEYPRESS_NOTIFICATION_VSEVT_CODE          0x040AU

/* Vendor specific codes of ACI GATT/ATT events
 */

/* ACI_GATT_ATTRIBUTE_MODIFIED_EVENT code */
#define ACI_GATT_ATTRIBUTE_MODIFIED_VSEVT_CODE            0x0C01U

/* ACI_GATT_PROC_TIMEOUT_EVENT code */
#define ACI_GATT_PROC_TIMEOUT_VSEVT_CODE                  0x0C02U

/* ACI_ATT_EXCHANGE_MTU_RESP_EVENT code */
#define ACI_ATT_EXCHANGE_MTU_RESP_VSEVT_CODE              0x0C03U

/* ACI_ATT_FIND_INFO_RESP_EVENT code */
#define ACI_ATT_FIND_INFO_RESP_VSEVT_CODE                 0x0C04U

/* ACI_ATT_FIND_BY_TYPE_VALUE_RESP_EVENT code */
#define ACI_ATT_FIND_BY_TYPE_VALUE_RESP_VSEVT_CODE        0x0C05U

/* ACI_ATT_READ_BY_TYPE_RESP_EVENT code */
#define ACI_ATT_READ_BY_TYPE_RESP_VSEVT_CODE              0x0C06U

/* ACI_ATT_READ_RESP_EVENT code */
#define ACI_ATT_READ_RESP_VSEVT_CODE                      0x0C07U

/* ACI_ATT_READ_BLOB_RESP_EVENT code */
#define ACI_ATT_READ_BLOB_RESP_VSEVT_CODE                 0x0C08U

/* ACI_ATT_READ_MULTIPLE_RESP_EVENT code */
#define ACI_ATT_READ_MULTIPLE_RESP_VSEVT_CODE             0x0C09U

/* ACI_ATT_READ_BY_GROUP_TYPE_RESP_EVENT code */
#define ACI_ATT_READ_BY_GROUP_TYPE_RESP_VSEVT_CODE        0x0C0AU

/* ACI_ATT_PREPARE_WRITE_RESP_EVENT code */
#define ACI_ATT_PREPARE_WRITE_RESP_VSEVT_CODE             0x0C0CU

/* ACI_ATT_EXEC_WRITE_RESP_EVENT code */
#define ACI_ATT_EXEC_WRITE_RESP_VSEVT_CODE                0x0C0DU

/* ACI_GATT_INDICATION_EVENT code */
#define ACI_GATT_INDICATION_VSEVT_CODE                    0x0C0EU

/* ACI_GATT_NOTIFICATION_EVENT code */
#define ACI_GATT_NOTIFICATION_VSEVT_CODE                  0x0C0FU

/* ACI_GATT_PROC_COMPLETE_EVENT code */
#define ACI_GATT_PROC_COMPLETE_VSEVT_CODE                 0x0C10U

/* ACI_GATT_ERROR_RESP_EVENT code */
#define ACI_GATT_ERROR_RESP_VSEVT_CODE                    0x0C11U

/* ACI_GATT_DISC_READ_CHAR_BY_UUID_RESP_EVENT code */
#define ACI_GATT_DISC_READ_CHAR_BY_UUID_RESP_VSEVT_CODE   0x0C12U

/* ACI_GATT_WRITE_PERMIT_REQ_EVENT code */
#define ACI_GATT_WRITE_PERMIT_REQ_VSEVT_CODE              0x0C13U

/* ACI_GATT_READ_PERMIT_REQ_EVENT code */
#define ACI_GATT_READ_PERMIT_REQ_VSEVT_CODE               0x0C14U

/* ACI_GATT_READ_MULTI_PERMIT_REQ_EVENT code */
#define ACI_GATT_READ_MULTI_PERMIT_REQ_VSEVT_CODE         0x0C15U

/* ACI_GATT_TX_POOL_AVAILABLE_EVENT code */
#define ACI_GATT_TX_POOL_AVAILABLE_VSEVT_CODE             0x0C16U

/* ACI_GATT_SERVER_CONFIRMATION_EVENT code */
#define ACI_GATT_SERVER_CONFIRMATION_VSEVT_CODE           0x0C17U

/* ACI_GATT_PREPARE_WRITE_PERMIT_REQ_EVENT code */
#define ACI_GATT_PREPARE_WRITE_PERMIT_REQ_VSEVT_CODE      0x0C18U

/* ACI_GATT_EATT_BEARER_EVENT code */
#define ACI_GATT_EATT_BEARER_VSEVT_CODE                   0x0C19U

/* ACI_GATT_MULT_NOTIFICATION_EVENT code */
#define ACI_GATT_MULT_NOTIFICATION_VSEVT_CODE             0x0C1AU

/* ACI_GATT_NOTIFICATION_COMPLETE_EVENT code */
#define ACI_GATT_NOTIFICATION_COMPLETE_VSEVT_CODE         0x0C1BU

/* ACI_GATT_READ_EXT_EVENT code */
#define ACI_GATT_READ_EXT_VSEVT_CODE                      0x0C1DU

/* ACI_GATT_INDICATION_EXT_EVENT code */
#define ACI_GATT_INDICATION_EXT_VSEVT_CODE                0x0C1EU

/* ACI_GATT_NOTIFICATION_EXT_EVENT code */
#define ACI_GATT_NOTIFICATION_EXT_VSEVT_CODE              0x0C1FU

/* Vendor specific codes of ACI L2CAP events
 */

/* ACI_L2CAP_CONNECTION_UPDATE_RESP_EVENT code */
#define ACI_L2CAP_CONNECTION_UPDATE_RESP_VSEVT_CODE       0x0800U

/* ACI_L2CAP_PROC_TIMEOUT_EVENT code */
#define ACI_L2CAP_PROC_TIMEOUT_VSEVT_CODE                 0x0801U

/* ACI_L2CAP_CONNECTION_UPDATE_REQ_EVENT code */
#define ACI_L2CAP_CONNECTION_UPDATE_REQ_VSEVT_CODE        0x0802U

/* ACI_L2CAP_COMMAND_REJECT_EVENT code */
#define ACI_L2CAP_COMMAND_REJECT_VSEVT_CODE               0x080AU

/* ACI_L2CAP_COC_CONNECT_EVENT code */
#define ACI_L2CAP_COC_CONNECT_VSEVT_CODE                  0x0810U

/* ACI_L2CAP_COC_CONNECT_CONFIRM_EVENT code */
#define ACI_L2CAP_COC_CONNECT_CONFIRM_VSEVT_CODE          0x0811U

/* ACI_L2CAP_COC_RECONF_EVENT code */
#define ACI_L2CAP_COC_RECONF_VSEVT_CODE                   0x0812U

/* ACI_L2CAP_COC_RECONF_CONFIRM_EVENT code */
#define ACI_L2CAP_COC_RECONF_CONFIRM_VSEVT_CODE           0x0813U

/* ACI_L2CAP_COC_DISCONNECT_EVENT code */
#define ACI_L2CAP_COC_DISCONNECT_VSEVT_CODE               0x0814U

/* ACI_L2CAP_COC_FLOW_CONTROL_EVENT code */
#define ACI_L2CAP_COC_FLOW_CONTROL_VSEVT_CODE             0x0815U

/* ACI_L2CAP_COC_RX_DATA_EVENT code */
#define ACI_L2CAP_COC_RX_DATA_VSEVT_CODE                  0x0816U

/* ACI_L2CAP_COC_TX_POOL_AVAILABLE_EVENT code */
#define ACI_L2CAP_COC_TX_POOL_AVAILABLE_VSEVT_CODE        0x0817U

/* Vendor specific codes of ACI HAL events
 */

/* ACI_HAL_END_OF_RADIO_ACTIVITY_EVENT code */
#define ACI_HAL_END_OF_RADIO_ACTIVITY_VSEVT_CODE          0x0004U

/* ACI_HAL_SCAN_REQ_REPORT_EVENT code */
#define ACI_HAL_SCAN_REQ_REPORT_VSEVT_CODE                0x0005U

/* ACI_HAL_FW_ERROR_EVENT code */
#define ACI_HAL_FW_ERROR_VSEVT_CODE                       0x0006U


#endif /* BLE_VS_CODES_H__ */
