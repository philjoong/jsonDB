import urllib.request
import urllib.parse
import json

BASE = "http://10.246.42.51:8000"

def get(path, params=None):
    url = f"{BASE}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    print(f"\n>>> GET {url}")
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req) as res:
        data = res.read().decode("utf-8")
        try:
            print(json.dumps(json.loads(data), indent=2, ensure_ascii=False))
        except json.JSONDecodeError:
            print(data)

def post(path, data=None, content_type="application/x-www-form-urlencoded"):
    url = f"{BASE}{path}"
    print(f"\n>>> POST {url}")
    body = data.encode("utf-8") if data else b""
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", content_type)
    with urllib.request.urlopen(req) as res:
        data = res.read().decode("utf-8")
        try:
            print(json.dumps(json.loads(data), indent=2, ensure_ascii=False))
        except json.JSONDecodeError:
            print(data)

# =============================================================================
# Account API
# =============================================================================

# 계정 목록 조회
# get("/api/account/accounts", {"size": 10})

# # 계정 조회 (단건)
# get("/api/account/accounts/muktop@ncsoft.com")

# # 법인 목록 조회
# get("/api/account/companies", {"size": 10, "sort": "displayOrder:asc"})

# # 법인별 부서 목록 조회
# get("/api/account/companies/NCK/departments", {"size": 10, "visibleType": "VISIBLE"})

# # 법인,부서별 부서 조회 (단건)
# get("/api/account/companies/NCK/departments/10008624")

# # 부서 하이라키 조회
# get("/api/account/companies/NCK/departments:hierarchy", {"parentCode": "1", "visibleType": "VISIBLE"})

# # 법인별 임직원 조회
# get("/api/account/companies/NCK/departments/-/accounts", {"size": 10})

# # 법인,userPrincipalName별 임직원 조회
# get("/api/account/companies/NCK/departments/-/accounts/muktop@ncsoft.com")

# # 임직원 프로필 사진
# get("/api/account/companies/NCK/departments/-/accounts/muktop@ncsoft.com:profile-image")

# # 법인,부서별 임직원 조회
# get("/api/account/companies/NCK/departments/10008624/accounts", {"size": 10})

# # 법인,부서,userPrincipalName별 임직원 조회
# get("/api/account/companies/NCK/departments/10008624/accounts/muktop@ncsoft.com")

# =============================================================================
# Email API
# =============================================================================

# # Tenant 단위 메일 전송 내역 조회
# get("/api/email/users/-/emails", {"size": 10, "sendStatus": "SUCCESS"})

# # 메일 전송 내역 조회 (상태)
# get("/api/email/users/-/emails:status", {"id[]": "018fa98e-f56f-7a10-91ea-780d3d9d72f2"})

# # 사용자 단위 메일 전송 내역 조회
# get("/api/email/users/muktop@ncsoft.com/emails", {"size": 10})

# # 메일 전송 내역 조회 (단건)
# get("/api/email/users/muktop@ncsoft.com/emails/018fa98f-3454-7fe0-bf06-a8a2f37bccd4:status")

# # 메일함 조회 (카운트)
# get("/api/email/users/muktop@ncsoft.com/emails:count", {"channelType": "AZURE_GRAPH"})

# # 사용자별 메일 전송 상태 조회
# get("/api/email/users/muktop@ncsoft.com/emails:status", {"id[]": "018fa98e-f56f-7a10-91ea-780d3d9d72f2"})

# 메일 발송 (동기)
post("/api/email/users/muktop@ncsoft.com/emails",
     urllib.parse.urlencode({
         "subject": "테스트_제목_입니다",
         "contentType": "TEXT",
         "content": "테스트_내용_입니다",
         "toRecipients[]": "muktop@ncsoft.com",
     }))

# # 비동기 메일 발송
# post("/api/email/users/muktop@ncsoft.com/emails:async",
#      urllib.parse.urlencode({
#          "subject": "테스트_제목_입니다",
#          "contentType": "TEXT",
#          "content": "테스트_내용_입니다",
#          "toRecipients[]": "muktop@ncsoft.com",
#      }))

# # 메일 발송 취소
# post("/api/email/users/muktop@ncsoft.com/emails/메일ID여기에입력:cancel")

# # 메일 재발송
# post("/api/email/emails/muktop@ncsoft.com/emails/메일ID여기에입력:resend")
