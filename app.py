import streamlit as st
import pandas as pd
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from bs4 import BeautifulSoup
import io
import time
import sys
import asyncio

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

st.set_page_config(page_title="인트라넷 데이터 수집기", page_icon="📊", layout="centered")

def clean_numeric_data(df):
    """
    DataFrame에서 숫자 데이터를 포함할 가능성이 있는 열의 콤마나 통화 기호를 제거하고 숫자형으로 변환.
    """
    # Flatten MultiIndex columns if they exist (prevents Excel export errors)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = ['_'.join(map(str, col)).strip() for col in df.columns.values]

    for col in df.columns:
        # Check if the column is of object type (strings)
        if df[col].dtype == 'object':
            try:
                # Remove commas globally
                cleaned = df[col].str.replace(',', '', regex=False)
                # Remove common currency symbols
                cleaned = cleaned.str.replace('₩', '', regex=False)
                cleaned = cleaned.str.replace('$', '', regex=False)
                # Convert to numeric, errors='ignore' keeps strings if they can't be converted
                df[col] = pd.to_numeric(cleaned, errors='ignore')
            except Exception as e:
                pass
    return df

def scrape_sales_data(user_id, password):
    """
    Playwright를 사용하여 인트라넷에 로그인하고 예상마감 데이터를 크롤링합니다.
    """
    with sync_playwright() as p:
        # headless=False로 설정하여 실행 과정을 화면에 보여줌
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            viewport={'width': 1280, 'height': 800},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        )
        page = context.new_page()
        
        try:
            # 1. 로그인 페이지 접속
            st.info("로그인 페이지로 이동 중...")
            page.goto("http://intra.emnet.co.kr/")
            
            # ID 및 패스워드 입력란 대기 (범용적인 선택자 사용)
            st.info("로그인 정보 입력 중...")
            page.wait_for_selector('input', timeout=10000)
            
            try:
                # 텍스트 인풋 중 ID 입력란으로 추정되는 요소에 포커스
                id_inputs = page.locator('input[type="text"], input[name*="id" i]')
                if id_inputs.count() > 0:
                    id_inputs.first.fill(user_id)
                else:
                    page.locator('input').nth(0).fill(user_id) # 최후의 수단
                    
                pw_inputs = page.locator('input[type="password"]')
                if pw_inputs.count() > 0:
                    pw_inputs.first.fill(password)
                else:
                    page.locator('input').nth(1).fill(password) # 최후의 수단
            except Exception as e:
                st.warning("로그인 필드를 찾는데 어려움이 있습니다. 사이트 구조가 특이할 수 있습니다.")
                page.locator('input').nth(0).fill(user_id)
                page.locator('input').nth(1).fill(password)
                
            # 로그인 버튼 클릭 (submit 타입 또는 텍스트 '로그인' 포함 요소)
            try:
                page.locator('button[type="submit"], input[type="submit"], button:has-text("로그인")').first.click()
            except:
                # 만약 버튼을 못 찾으면 폼을 엔터키로 제출 시도
                page.keyboard.press("Enter")
            
            # 로그인 완료를 기다림 (네트워크 유휴 상태 또는 페이지 이동)
            try:
                page.wait_for_load_state("networkidle", timeout=5000)
            except:
                pass # 타임아웃되어도 무시하고 진행 시도
                
            # 에러 메시지(경고창 등) 체크 로직을 넣을 수도 있으나, 여기서는 페이지 이동으로 성공 여부 판단
            
            # 2. 목표 페이지 탭 클릭 및 이동
            st.info("예상마감 데이터 조회 탭으로 이동 중...")
            try:
                # 직접 URL 이동
                target_url = "http://intra.emnet.co.kr/?p=pages/t-team/expected-sales-manage"
                page.goto(target_url)
                page.wait_for_load_state("networkidle", timeout=5000)
                
                # 명시적으로 '예상마감 데이터 조회' 탭 클릭
                tab_locator = page.locator('a:has-text("예상마감 데이터 조회"), button:has-text("예상마감 데이터 조회"), li:has-text("예상마감 데이터 조회")').last
                if tab_locator.is_visible(timeout=3000):
                    tab_locator.click()
                    page.wait_for_load_state("networkidle", timeout=5000)
                    time.sleep(1)
            except Exception as e:
                pass
            
            # 3. 조회/검색 버튼 클릭
            st.info("검색 버튼 클릭 및 데이터 로딩 대기 중...")
            try:
                # '조회' 또는 '검색' 버튼 클릭
                search_btn = page.locator('button:has-text("조회"), button:has-text("검색"), input[value="조회"], input[value="검색"], a:has-text("조회"), a:has-text("검색")').first
                if search_btn.is_visible(timeout=3000):
                    search_btn.click()
                    page.wait_for_load_state("networkidle")
                    time.sleep(2) # 비동기 로딩을 위한 여유 대기
            except Exception as e:
                # 버튼이 없거나 찾지 못하면 그냥 진행
                pass
            
            # 4. 데이터 크롤링
            st.info("테이블 데이터 추출 중...")
            try:
                # 테이블이 나타날 때까지 대기
                page.wait_for_selector("table", timeout=10000)
            except PlaywrightTimeoutError:
                st.warning("테이블 로딩 확인이 지연되고 있습니다. 현재 화면에 표시된 데이터로 추출을 시도합니다.")
            
            time.sleep(2) # 렌더링 완료 및 애니메이션 대기를 위한 강제 대기
            
            # HTML 내용 가져오기
            html_content = page.content()
            
            # BeautifulSoup으로 테이블 파싱 (한 줄 한 줄 정밀하게)
            try:
                soup = BeautifulSoup(html_content, 'html.parser')
                tables = soup.find_all('table')
                if not tables:
                    st.error("HTML에서 테이블을 찾지 못했습니다.")
                    return None
                    
                # 여러 테이블 중 가장 큰(내용이 많은) 테이블 선택
                main_table = max(tables, key=lambda t: len(t.text))
                
                # 1. 헤더 추출 로직
                headers = []
                thead = main_table.find('thead')
                if thead:
                    header_rows = thead.find_all('tr')
                    if len(header_rows) == 1:
                        headers = [th.get_text(separator=" ", strip=True) for th in header_rows[0].find_all(['th', 'td'])]
                    elif len(header_rows) > 1:
                        # 다중 행 헤더일 경우 텍스트를 이어붙입니다 (가장 일반적인 경우)
                        headers_row1 = [th.get_text(separator=" ", strip=True) for th in header_rows[0].find_all(['th', 'td'])]
                        headers_row2 = [th.get_text(separator=" ", strip=True) for th in header_rows[1].find_all(['th', 'td'])]
                        
                        # 길이가 다를 수 있으므로 안전하게 병합 (단순화된 병합 로직)
                        max_len = max(len(headers_row1), len(headers_row2))
                        headers_row1.extend([''] * (max_len - len(headers_row1)))
                        headers_row2.extend([''] * (max_len - len(headers_row2)))
                        
                        headers = [f"{h1}_{h2}".strip('_') for h1, h2 in zip(headers_row1, headers_row2)]
                else:
                    # thead가 없다면 첫 번째 tr을 헤더로 취급
                    first_tr = main_table.find('tr')
                    if first_tr:
                        headers = [th.get_text(separator=" ", strip=True) for th in first_tr.find_all(['th', 'td'])]

                # 2. 본문(데이터) 추출 로직
                data_rows = []
                tbody = main_table.find('tbody')
                rows = tbody.find_all('tr') if tbody else main_table.find_all('tr')[1:] # thead가 없으면 첫줄 제외
                
                for tr in rows:
                    cells = tr.find_all(['td', 'th'])
                    row_data = []
                    for cell in cells:
                        # <br> 태그를 실제 줄바꿈(\n)으로 변경하여 텍스트 추출
                        for br in cell.find_all('br'):
                            br.replace_with('\n')
                        
                        # 셀 텍스트를 추출하고 양쪽 공백 제거
                        text = cell.get_text(separator="\n", strip=True)
                        row_data.append(text)
                    data_rows.append(row_data)

                if not data_rows:
                    st.warning("테이블 내에 데이터 행이 없습니다.")
                    return None

                # 데이터의 열 개수와 헤더의 열 개수가 맞지 않을 경우 대비
                if headers and len(headers) < len(data_rows[0]):
                    headers.extend([f"Column_{i}" for i in range(len(headers), len(data_rows[0]))])
                elif headers and len(headers) > len(data_rows[0]):
                    headers = headers[:len(data_rows[0])]
                
                # 데이터프레임 생성
                if not headers:
                    headers = [f"Col_{i}" for i in range(len(data_rows[0]))]
                main_df = pd.DataFrame(data_rows, columns=headers)
                
                # 데이터 클리닝
                cleaned_df = clean_numeric_data(main_df)
                return cleaned_df
                
            except Exception as e:
                st.error(f"테이블 파싱 중 오류 발생: {e}")
                return None
                
        except Exception as e:
            st.error(f"실행 중 오류가 발생했습니다: {e}")
            return None
        finally:
            browser.close()

# --- Streamlit UI 구성 ---

st.title("📊 인트라넷 판매 데이터 자동 수집기")
st.markdown("""
이 프로그램은 브라우저 자동화를 통해 인트라넷에 접속하여, **예상마감 데이터**를 자동으로 조회 및 추출합니다.
추출된 데이터의 콤마나 통화 기호는 숫자로 자동 변환되어 엑셀 파일로 다운로드할 수 있습니다.
""")

st.header("1. 인트라넷 로그인 정보 입력")
with st.form("login_form"):
    user_id = st.text_input("아이디 (ID)", placeholder="인트라넷 아이디를 입력하세요")
    password = st.text_input("비밀번호 (Password)", type="password", placeholder="비밀번호를 입력하세요")
    
    submit_button = st.form_submit_button("▶ 데이터 수집 시작")

if submit_button:
    if not user_id or not password:
        st.warning("⚠️ 아이디와 비밀번호를 모두 입력해주세요.")
    else:
        status_container = st.container()
        with status_container:
            with st.spinner("로봇이 브라우저를 열고 데이터를 수집 중입니다... (창이 열리면 닫지 마세요!)"):
                result_df = scrape_sales_data(user_id, password)
                
        if result_df is not None and not result_df.empty:
            st.success(f"✅ 데이터 수집이 성공적으로 완료되었습니다! (총 {len(result_df)}행)")
            
            # 수집된 데이터 미리보기
            st.subheader("👀 데이터 미리보기 (상위 10개)")
            st.dataframe(result_df.head(10))
            
            # 엑셀 변환 (메모리 버퍼)
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='openpyxl') as writer:
                result_df.to_excel(writer, index=False, sheet_name='Sales Data')
            
            excel_data = output.getvalue()
            
            st.subheader("2. 엑셀 파일 다운로드")
            st.download_button(
                label="📥 엑셀(Excel) 파일 다운로드",
                data=excel_data,
                file_name="예상마감_데이터.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True
            )
        elif result_df is not None and result_df.empty:
            st.warning("수집된 데이터가 없습니다. (테이블이 비어있음)")
        else:
            st.error("데이터 수집에 실패했습니다. 위의 오류 메시지를 확인해주세요.")
