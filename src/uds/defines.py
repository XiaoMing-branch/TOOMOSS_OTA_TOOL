"""
ISO 14229-1 (UDS) 常量定义与 NRC 负响应解释字典
"""

# ----------------- UDS 服务 ID (SID) -----------------
SID_DIAGNOSTIC_SESSION_CONTROL    = 0x10
SID_ECU_RESET                     = 0x11
SID_CLEAR_DIAGNOSTIC_INFORMATION  = 0x14
SID_READ_DTC_INFORMATION          = 0x19
SID_READ_DATA_BY_IDENTIFIER       = 0x22
SID_SECURITY_ACCESS               = 0x27
SID_COMMUNICATION_CONTROL         = 0x28
SID_WRITE_DATA_BY_IDENTIFIER      = 0x2E
SID_ROUTINE_CONTROL               = 0x31
SID_REQUEST_DOWNLOAD              = 0x34
SID_TRANSFER_DATA                 = 0x36
SID_REQUEST_TRANSFER_EXIT         = 0x37
SID_TESTER_PRESENT                = 0x3E
SID_CONTROL_DTC_SETTING           = 0x85
SID_NEGATIVE_RESPONSE             = 0x7F

# ----------------- 0x10 会话控制子功能 -----------------
SESSION_DEFAULT                   = 0x01
SESSION_PROGRAMMING               = 0x02
SESSION_EXTENDED                  = 0x03

# ----------------- 0x11 ECU 复位子功能 -----------------
RESET_HARD                        = 0x01
RESET_KEY_OFF_ON                  = 0x02
RESET_SOFT                        = 0x03

# ----------------- 0x85 控制 DTC 记录 -----------------
DTC_SETTING_ON                    = 0x01
DTC_SETTING_OFF                   = 0x02

# ----------------- 0x31 例程控制子功能 -----------------
ROUTINE_START                     = 0x01
ROUTINE_STOP                      = 0x02
ROUTINE_REQUEST_RESULTS           = 0x03

# 常用例程 ID (RID)
RID_CHECK_PROGRAM_CONDITIONS      = 0x0203  # 刷写条件检测
RID_ERASE_APP_FLASH               = 0xFF00  # 擦除 Flash
RID_CHECK_COMPATIBILITY           = 0xFF01  # 刷写完成兼容性/有效性检测
RID_SECURITY_SIGN_CHECK           = 0xDD02  # 安全签名校验 (Flash Drv)
RID_STAY_IN_BOOT                  = 0xDD01  # StayInBoot 驻留

# ----------------- 负响应代码 (NRC) -----------------
NRC_POSITIVE_RESPONSE             = 0x00
NRC_GENERAL_REJECT                = 0x10
NRC_SERVICE_NOT_SUPPORTED         = 0x11
NRC_SUBFUNCTION_NOT_SUPPORTED     = 0x12
NRC_INCORRECT_MESSAGE_LENGTH      = 0x13
NRC_RESPONSE_TOO_LONG             = 0x14
NRC_BUSY_REPEAT_REQUEST           = 0x21
NRC_CONDITIONS_NOT_CORRECT        = 0x22
NRC_REQUEST_SEQUENCE_ERROR        = 0x24
NRC_REQUEST_OUT_OF_RANGE          = 0x31
NRC_SECURITY_ACCESS_DENIED        = 0x33
NRC_INVALID_KEY                   = 0x35
NRC_EXCEED_NUMBER_OF_ATTEMPTS     = 0x36
NRC_REQUIRED_TIME_DELAY_NOT_EXP   = 0x37
NRC_UPLOAD_DOWNLOAD_NOT_ACCEPTED  = 0x70
NRC_TRANSFER_DATA_SUSPENDED       = 0x71
NRC_GENERAL_PROGRAMMING_FAILURE   = 0x72
NRC_WRONG_BLOCK_SEQUENCE_COUNTER  = 0x73
NRC_RESPONSE_PENDING              = 0x78

NRC_MAP = {
    NRC_GENERAL_REJECT: "通用拒绝 (General Reject)",
    NRC_SERVICE_NOT_SUPPORTED: "服务不支持 (Service Not Supported)",
    NRC_SUBFUNCTION_NOT_SUPPORTED: "子功能不支持 (Sub-function Not Supported)",
    NRC_INCORRECT_MESSAGE_LENGTH: "报文长度或格式不正确 (Incorrect Message Length)",
    NRC_RESPONSE_TOO_LONG: "响应报文过长 (Response Too Long)",
    NRC_BUSY_REPEAT_REQUEST: "服务器正忙，请稍后重发 (Busy Repeat Request)",
    NRC_CONDITIONS_NOT_CORRECT: "前置条件不满足 (Conditions Not Correct)",
    NRC_REQUEST_SEQUENCE_ERROR: "请求顺序错误 (Request Sequence Error)",
    NRC_REQUEST_OUT_OF_RANGE: "参数或地址超出范围 (Request Out Of Range)",
    NRC_SECURITY_ACCESS_DENIED: "安全访问被拒绝 (Security Access Denied)",
    NRC_INVALID_KEY: "安全密钥无效 (Invalid Key)",
    NRC_EXCEED_NUMBER_OF_ATTEMPTS: "尝试解锁次数超限 (Exceeded Attempts)",
    NRC_REQUIRED_TIME_DELAY_NOT_EXP: "安全访问冷却延时未到 (Required Time Delay Not Expired)",
    NRC_UPLOAD_DOWNLOAD_NOT_ACCEPTED: "上传/下载请求被拒绝 (Upload/Download Not Accepted)",
    NRC_TRANSFER_DATA_SUSPENDED: "数据传输挂起 (Transfer Data Suspended)",
    NRC_GENERAL_PROGRAMMING_FAILURE: "编程常规故障 (General Programming Failure)",
    NRC_WRONG_BLOCK_SEQUENCE_COUNTER: "数据块序号错误 (Wrong Block Sequence Counter)",
    NRC_RESPONSE_PENDING: "等待响应中 (Response Pending - 0x78)",
}


def get_nrc_description(nrc: int) -> str:
    return NRC_MAP.get(nrc, f"未知负响应代码 (0x{nrc:02X})")
