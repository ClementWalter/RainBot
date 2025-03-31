import logging
import os
import time
from datetime import datetime
from tempfile import NamedTemporaryFile

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from selenium import webdriver
from selenium.common.exceptions import NoSuchElementException, TimeoutException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from twocaptcha import TwoCaptcha
from webdriver_manager.chrome import ChromeDriverManager

load_dotenv()
BOOKING_URL = os.environ["BOOKING_URL"]
LOGIN_URL = os.environ["LOGIN_URL"]
AUTH_BASE_URL = os.environ["AUTH_BASE_URL"]
ACCOUNT_BASE_URL = os.environ["ACCOUNT_BASE_URL"]
CAPTCHA_URL = os.environ["CAPTCHA_URL"]
CAPTCHA_API_KEY = os.environ["CAPTCHA_API_KEY"]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger(__name__)


class BookingService:
    def __init__(self):
        self._username = None
        self._is_booking = False
        self.reservation = {}
        self._query_data = {}
        self._setup_driver()

    def _setup_driver(self):
        """Set up the Selenium WebDriver with appropriate options for visible operation."""
        chrome_options = Options()

        # Create a unique temporary directory for this session
        import tempfile
        import uuid

        temp_dir = tempfile.mkdtemp()
        user_data_dir = f"{temp_dir}/chrome-data-{uuid.uuid4()}"

        chrome_options.add_argument(f"--user-data-dir={user_data_dir}")
        chrome_options.add_argument("--headless=new")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--window-size=1920,1080")
        chrome_options.add_argument(
            "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.6998.165 Safari/537.36"
        )

        # For Heroku deployment
        if os.environ.get("GOOGLE_CHROME_BIN"):
            chrome_options.binary_location = os.environ["GOOGLE_CHROME_BIN"]

        try:
            if os.environ.get("CHROMEDRIVER_PATH"):
                service = Service(os.environ["CHROMEDRIVER_PATH"])
                self.driver = webdriver.Chrome(service=service, options=chrome_options)
            else:
                service = Service(ChromeDriverManager().install())
                self.driver = webdriver.Chrome(service=service, options=chrome_options)

            # Set page load timeout
            self.driver.set_page_load_timeout(30)
            self.wait = WebDriverWait(self.driver, 10)

        except Exception as e:
            logger.error(f"Failed to initialize WebDriver: {str(e)}")
            raise

        # Store the temp directory path for cleanup
        self._temp_dir = temp_dir

    @staticmethod
    def find_courts_without_login(places, match_day, in_out, hour_from, hour_to, *_, **__):
        """
        Args:
            places (list): places where to look spot in
            match_day (str): dd/mm/YYYY
            in_out (list): containing V, F both or None
            hour_from (str): beginning of the spot
            hour_to (str): end of the spot
        """
        search_data = {
            "where": places,
            "selWhereTennisName": places,
            "when": match_day,
            "selCoating": ["96", "2095", "94", "1324", "2016", "92"],
            "selInOut": in_out,
            "hourRange": f"{int(hour_from)}-{int(hour_to)}",
        }
        response = requests.post(
            BOOKING_URL,
            search_data,
            params={"page": "recherche", "action": "rechercher_creneau"},
            timeout=10,
        )
        soup = BeautifulSoup(response.text, features="html5lib")
        courts = [court for court in soup.find_all("h4", {"class": "panel-title"})]
        if not courts:
            return None, None
        court = courts[0]
        place = [
            p
            for p in places
            if p.replace(" ", "")
            == court.find_parent("div", attrs={"role": "tabpanel"}).attrs["id"]
        ][0]
        return place, int(court.text[:2])

    def book_court(
        self,
        username,
        password,
        place,
        match_day,
        in_out,
        hour_from,
        hour_to,
        partenaire_first_name,
        partenaire_last_name,
        *_,
        **__,
    ):
        self.login(username, password)
        self.driver.save_screenshot("after_login.png")
        if self.has_booking():
            logger.info("Already has a booking")
            return None

        self.search_courts(place, match_day, in_out, hour_from, hour_to)
        self.driver.save_screenshot("after_search.png")

        # Wait for the booking button to be present
        try:
            self.wait.until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "button.buttonAllOk")),
                message="Booking button not found within the expected time.",
            )
        except TimeoutException:
            logger.error("buttonAllOk not found")
            return None

        booking_buttons = self.driver.find_elements(By.CSS_SELECTOR, "button.buttonAllOk")
        if booking_buttons:
            booking_buttons[0].click()
            time.sleep(0.5)
        else:
            logger.error("No booking buttons found")
            return None

        # Solve captcha
        logger.info("Solving captcha")
        try:
            self.driver.save_screenshot("before_solving_captcha.png")
            self.solve_captcha()
            self.driver.save_screenshot("after_solving_captcha.png")
        except Exception as e:
            self.driver.save_screenshot("error_solving_captcha.png")
            logger.error(f"Error solving captcha: {str(e)}")
            raise
        time.sleep(5)

        # Fill player details
        logger.info("Filling player details")
        try:
            self.driver.save_screenshot("before_filling_player_details.png")
            self.fill_player_details(partenaire_first_name, partenaire_last_name, self._username)
            self.driver.save_screenshot("after_filling_player_details.png")
        except Exception as e:
            self.driver.save_screenshot("error_filling_player_details.png")
            logger.error(f"Error filling player details: {str(e)}")
            raise

        self.driver.save_screenshot("before_clicking_ticket_option.png")
        ticket_option = self.driver.find_element(By.ID, "submitControle")
        ticket_option.click()
        self.driver.save_screenshot("after_clicking_ticket_option.png")

        self.driver.save_screenshot("before_clicking_payment_option.png")
        payment_option = self.driver.find_element(
            By.CSS_SELECTOR,
            "table.price-item.text-center.option[paymentmode='existingTicket'][nbtickets='1']",
        )
        # Click the table to select the payment option
        payment_option.click()
        self.driver.save_screenshot("after_clicking_payment_option.png")

        # Submit payment
        self.driver.save_screenshot("before_submitting_payment.png")
        submit_button = self.driver.find_element(By.CSS_SELECTOR, "button[type='submit']")
        submit_button.click()
        self.driver.save_screenshot("after_submitting_payment.png")

        # Wait for payment processing
        self.wait.until(EC.url_contains("reservation"))
        self.driver.save_screenshot("after_waiting_for_payment_processing.png")

        message = f"Court successfully paid for {username}"
        logger.log(logging.INFO, message)

    def login(self, username, password):
        """Log in to the booking system."""
        # Navigate to login page
        logger.info(f"Navigating to login page: {LOGIN_URL}")
        self.driver.get(LOGIN_URL)

        # Wait for login form to load
        logger.info("Waiting for login form to load")
        try:
            self.wait.until(EC.presence_of_element_located((By.ID, "form-login")))
            logger.info("Login form loaded successfully")
        except TimeoutException:
            logger.error("Login form not found and not already logged in")
            raise

        # Find the username and password fields
        username_input = self.driver.find_element(By.NAME, "username")
        password_input = self.driver.find_element(By.NAME, "password")

        # Clear the username field and type the username
        logger.info(f"Entering username: {username}")
        username_input.clear()
        username_input.send_keys(username)

        # Clear the password field and type the password
        logger.info("Entering password")
        password_input.clear()
        password_input.send_keys(password)

        # Find the submit button
        submit_button = self.driver.find_element(By.XPATH, "//button[@type='submit']")
        logger.info("Found submit button")

        # Click the submit button
        logger.info("Clicking submit button")
        submit_button.click()
        logger.info("Clicked submit button")

    def solve_captcha(self):
        self.driver.switch_to.default_content()

        try:
            WebDriverWait(self.driver, 5).until(
                EC.frame_to_be_available_and_switch_to_it((By.ID, "li-antibot-iframe"))
            )
        except TimeoutException:
            logger.error("Timeout waiting for captcha iframe")
            raise

        time.sleep(1)
        try:
            captcha_div = WebDriverWait(self.driver, 5).until(
                EC.presence_of_element_located((By.ID, "li-antibot-questions-container"))
            )
        except TimeoutException:
            self.driver.switch_to.default_content()
            self.driver.switch_to.frame(self.driver.find_element(By.ID, "li-antibot-iframe"))
            captcha_div = self.driver.find_element(By.ID, "li-antibot-questions-container")

        image_file = NamedTemporaryFile(suffix=".png", delete=False)
        captcha_div.screenshot(image_file.name)
        solver = TwoCaptcha(CAPTCHA_API_KEY)
        logger.info("Solving captcha")
        result = solver.normal(image_file.name)
        logger.info(f"Captcha solved: {result['code']}")
        captcha_input = self.driver.find_element(By.ID, "li-antibot-answer")
        captcha_input.clear()
        captcha_input.send_keys(result["code"])
        validate_button = self.driver.find_element(By.ID, "li-antibot-validate")
        validate_button.click()

        self.driver.switch_to.default_content()

    def logout(self):
        self.driver.quit()

    def fill_player_details(self, name, surname, email=None):
        """
        Fill in the player's name, surname, and email in the form.

        Args:
            name (str): The player's name.
            surname (str): The player's surname.
            email (str): The player's email.
        """
        # Locate the name input field within the 'name' div and fill it
        name_input = self.wait.until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "div.firstname input[name='player1']"))
        )
        name_input.clear()
        name_input.send_keys(name)
        logger.info(f"Filled name: {name}")

        # Locate the surname input field within the 'firstname' div and fill it
        surname_input = self.driver.find_element(By.CSS_SELECTOR, "div.name input[name='player1']")
        surname_input.clear()
        surname_input.send_keys(surname)
        logger.info(f"Filled surname: {surname}")

        # Locate the email input field within the 'email' div and fill it
        if email:
            email_input = self.driver.find_element(
                By.CSS_SELECTOR, "div.email input[name='player1']"
            )
            email_input.clear()
            email_input.send_keys(email)
            logger.info(f"Filled email: {email}")

    def search_courts(self, place, match_day, in_out, hour_from, hour_to):
        self.driver.get(f"{BOOKING_URL}?page=recherche&view=recherche_creneau")
        time.sleep(1)
        self.driver.save_screenshot("after_navigating_to_booking_page.png")

        # First, check for and close the initial modal popup
        try:
            # Wait for the modal to appear
            WebDriverWait(self.driver, 1).until(
                EC.presence_of_element_located((By.ID, "closePopup"))
            )

            # Find and click the close button or the "Je continue" button
            try:
                close_button = self.driver.find_element(By.ID, "closePopup")
                logger.info("Found closePopup button, clicking it")
                self.driver.execute_script("arguments[0].click();", close_button)
            except NoSuchElementException:
                try:
                    continue_button = self.driver.find_element(By.CSS_SELECTOR, ".popin.ignore")
                    logger.info("Found 'Je continue' button, clicking it")
                    self.driver.execute_script("arguments[0].click();", continue_button)
                except NoSuchElementException:
                    logger.warning("Could not find close or continue button on initial modal")

            # Wait for the modal to disappear
            time.sleep(1)

        except TimeoutException:
            logger.info("No initial modal popup found")

        # Wait for the search form to load
        # self.wait.until(EC.presence_of_element_located((By.NAME, "when")))

        # Check for and close any other modal dialogs
        try:
            modal = self.driver.find_element(By.ID, "confirmModalGeneral")
            if modal.is_displayed():
                logger.info("Modal dialog found, attempting to close it")
                close_button = self.driver.find_element(
                    By.CSS_SELECTOR, "#confirmModalGeneral .close"
                )
                self.driver.execute_script("arguments[0].click();", close_button)
                time.sleep(1)  # Wait for modal to close
        except NoSuchElementException:
            logger.info("No additional modal dialog found")

        # =====================================================================
        # PLACE SELECTION
        # =====================================================================

        # First, find the token input field - this is where we type
        token_input = self.driver.find_element(By.CSS_SELECTOR, "#whereToken .tokens-input-text")
        logger.info("Found token input field")

        # Clear any existing tokens first
        token_list = self.driver.find_element(By.ID, "whereToken")
        token_items = token_list.find_elements(By.CSS_SELECTOR, "li.tokens-list-token-holder")

        logger.info(f"Found {len(token_items)} existing tokens, removing them")
        for token in token_items:
            try:
                close_btn = token.find_element(By.CSS_SELECTOR, "span.tokens-delete-token")
                close_btn.click()  # Use normal click for human-like behavior
                logger.info("Removed a token")
                time.sleep(0.5)  # Small delay between removals
            except:
                logger.warning("Failed to remove a token")

        # Now add each place one by one, exactly as a human would
        logger.info(f"Adding place: {place}")

        # Focus on the input field
        token_input.click()
        time.sleep(0.2)

        # Clear any existing text
        token_input.clear()
        time.sleep(0.2)

        # Type the place name character by character with small delays
        for char in place:
            token_input.send_keys(char)

        # Wait for suggestions to appear - reduced wait time
        time.sleep(0.5)

        # Look for suggestions with the EXACT correct selector
        suggestions = self.driver.find_elements(
            By.CSS_SELECTOR, "li.tokens-suggestions-list-element"
        )

        logger.info(f"Found {len(suggestions)} suggestions for {place}")

        # Log the text of each suggestion for debugging
        for i, suggestion in enumerate(suggestions):
            try:
                suggestion_text = suggestion.text
                logger.info(f"Suggestion {i+1}: '{suggestion_text}'")

                # If this suggestion matches our place name, prioritize clicking it
                if place.lower() in suggestion_text.lower():
                    logger.info(f"Found matching suggestion: '{suggestion_text}'")
                    target_suggestion = suggestion
                    break
            except:
                logger.warning(f"Could not get text for suggestion {i+1}")
        else:
            # If no match found, use the first suggestion
            target_suggestion = suggestions[0]
            logger.info(f"Using first suggestion by default")

        # Click the suggestion
        target_suggestion.click()
        logger.info(f"Clicked on suggestion for {place}")

        # Wait for the token to be added - reduced wait time
        time.sleep(0.3)

        # Verify the token was added
        current_tokens = self.driver.find_elements(
            By.CSS_SELECTOR, "#whereToken li.tokens-list-token-holder"
        )
        logger.info(f"Current token count: {len(current_tokens)}")

        # =====================================================================
        # DATE SELECTION
        # =====================================================================

        # Set date
        logger.info(f"Setting date to: {match_day}")
        # The visible date input is readonly, but there's a hidden input that actually holds the value
        # First find both inputs
        visible_date_input = self.driver.find_element(By.ID, "when")
        hidden_date_input = self.driver.find_element(By.ID, "whenIso")

        logger.info(
            f"Found date inputs - visible: {visible_date_input.get_attribute('value')}, hidden: {hidden_date_input.get_attribute('value')}"
        )

        # Set the hidden input value directly using JavaScript
        self.driver.execute_script(f"document.getElementById('whenIso').value = '{match_day}';")
        logger.info(f"Set hidden date input value to {match_day}")

        # Also update the visible input for consistency
        # First get the formatted date (assuming match_day is in DD/MM/YYYY format)
        try:
            # Convert to a more readable format for the visible field
            date_obj = datetime.strptime(match_day, "%d/%m/%Y")
            formatted_date = date_obj.strftime("%A %d %B %Y")

            # Set the visible date
            self.driver.execute_script(
                f"document.getElementById('when').value = '{formatted_date}';"
            )
            logger.info(f"Set visible date input to {formatted_date}")
        except Exception as e:
            logger.warning(f"Could not format visible date: {str(e)}")

        # Trigger change events on both inputs to ensure the site recognizes the change
        self.driver.execute_script(
            """
            var hiddenInput = document.getElementById('whenIso');
            var visibleInput = document.getElementById('when');

            // Create and dispatch change events
            var event = new Event('change', { 'bubbles': true });
            hiddenInput.dispatchEvent(event);
            visibleInput.dispatchEvent(event);
        """
        )
        logger.info("Triggered change events on date inputs")

        # =====================================================================
        # INDOOR/OUTDOOR SELECTION
        # =====================================================================

        # Set in/out options (surface type)
        logger.info(f"Setting in/out options (surface type): {in_out}")
        # First click the dropdown button to open it
        dropdown_button = self.driver.find_element(By.ID, "dropdownTerrain")
        logger.info("Found dropdown button for terrain selection")

        # Click to open the dropdown
        dropdown_button.click()
        logger.info("Clicked dropdown button to open terrain options")
        time.sleep(0.3)

        # Now find the checkboxes inside the dropdown
        checkboxes = self.driver.find_elements(By.CSS_SELECTOR, "input[name='selInOut']")
        logger.info(f"Found {len(checkboxes)} terrain checkboxes")

        # Log the current state of checkboxes
        for i, checkbox in enumerate(checkboxes):
            value = checkbox.get_attribute("value")
            id_attr = checkbox.get_attribute("id")
            checked = checkbox.is_selected()
            logger.info(f"Terrain checkbox {i+1}: id={id_attr}, value={value}, checked={checked}")

        # We want to ensure only the options in in_out are checked
        for checkbox in checkboxes:
            value = checkbox.get_attribute("value")
            should_check = value in in_out
            is_checked = checkbox.is_selected()

            if should_check != is_checked:
                # Need to change the state
                try:
                    # Find the parent label and click it (more reliable than clicking the checkbox directly)
                    label = self.driver.find_element(
                        By.CSS_SELECTOR, f"label[for='{checkbox.get_attribute('id')}']"
                    )
                    label.click()
                    logger.info(
                        f"Clicked label for checkbox with value {value} to {'check' if should_check else 'uncheck'} it"
                    )
                    time.sleep(0.2)
                except Exception as e:
                    logger.warning(f"Could not click label: {str(e)}, trying direct checkbox click")
                    try:
                        # Try clicking the checkbox directly
                        checkbox.click()
                        logger.info(f"Clicked checkbox with value {value} directly")
                        time.sleep(0.2)
                    except Exception as e2:
                        logger.warning(f"Direct click failed: {str(e2)}, trying JavaScript")
                        # If direct click fails, try JavaScript
                        self.driver.execute_script(
                            "arguments[0].checked = arguments[1];", checkbox, should_check
                        )
                        logger.info(
                            f"Set checkbox with value {value} to {should_check} using JavaScript"
                        )

        # Click elsewhere to close the dropdown
        self.driver.find_element(By.TAG_NAME, "body").click()
        time.sleep(0.3)

        # =====================================================================
        # HOUR RANGE SELECTION
        # =====================================================================

        # Set hour range
        logger.info(f"Setting hour range: {hour_from} - {hour_to}")
        # Convert hour strings to integers for comparison
        hour_from_int = int(hour_from.split(":")[0] if ":" in hour_from else hour_from)
        hour_to_int = int(hour_to.split(":")[0] if ":" in hour_to else hour_to)

        # Find the slider element with the correct ID
        slider = self.driver.find_element(By.ID, "slider")
        logger.info("Found slider element with ID 'slider'")

        # Get the slider handles
        handles = slider.find_elements(By.CSS_SELECTOR, ".ui-slider-handle")
        if len(handles) >= 2:
            logger.info(f"Found {len(handles)} slider handles")

            # Get the current values from the tooltips
            tooltips = self.driver.find_elements(By.CSS_SELECTOR, ".tooltip-inner")
            if len(tooltips) >= 2:
                current_from = tooltips[0].text
                current_to = tooltips[1].text
                logger.info(f"Current slider range: {current_from} - {current_to}")

        # Calculate the positions based on the available range (8h to 22h = 14 hours)
        min_hour = 8  # The minimum hour on the slider
        max_hour = 22  # The maximum hour on the slider
        total_range = max_hour - min_hour

        # Calculate percentages for the handles
        from_percent = ((hour_from_int - min_hour) / total_range) * 100
        to_percent = ((hour_to_int - min_hour) / total_range) * 100

        logger.info(f"Setting slider handles to {from_percent}% and {to_percent}%")

        # Use JavaScript to set the slider values directly
        js_code = f"""
        // Set the slider handles
        var slider = $('#slider');
        if (slider.slider) {{
            // Set the values
            slider.slider('values', 0, {hour_from_int - min_hour});
            slider.slider('values', 1, {hour_to_int - min_hour});

            // Update the tooltips
            $('.tooltip1 .tooltip-inner').text('{hour_from_int}h');
            $('.tooltip2 .tooltip-inner').text('{hour_to_int}h');

            // Update the handle positions
            $('.ui-slider-handle').eq(0).css('left', '{from_percent}%');
            $('.ui-slider-handle').eq(1).css('left', '{to_percent}%');

            // Update the range
            $('.ui-slider-range').css({{
                'left': '{from_percent}%',
                'width': '{to_percent - from_percent}%'
            }});

            console.log('Set slider range to {hour_from_int}h-{hour_to_int}h');
            return true;
        }}
        return false;
        """

        success = self.driver.execute_script(js_code)
        if success:
            logger.info(
                f"Successfully set hour range using jQuery slider API: {hour_from_int}h - {hour_to_int}h"
            )
        else:
            logger.warning("jQuery slider API not available, trying direct DOM manipulation")

            # Try direct DOM manipulation
            js_direct = f"""
            // Set handle positions directly
            document.querySelectorAll('.ui-slider-handle')[0].style.left = '{from_percent}%';
            document.querySelectorAll('.ui-slider-handle')[1].style.left = '{to_percent}%';

            // Update tooltips
            document.querySelector('.tooltip1 .tooltip-inner').textContent = '{hour_from_int}h';
            document.querySelector('.tooltip2 .tooltip-inner').textContent = '{hour_to_int}h';

            // Update range
            var range = document.querySelector('.ui-slider-range');
            range.style.left = '{from_percent}%';
            range.style.width = '{to_percent - from_percent}%';
            """

            self.driver.execute_script(js_direct)
            logger.info(
                f"Set hour range using direct DOM manipulation: {hour_from_int}h - {hour_to_int}h"
            )

        # =====================================================================
        # SUBMIT SEARCH
        # =====================================================================

        # Add a short pause for manual inspection if need
        logger.info("Pausing briefly before search...")
        time.sleep(1.5)

        # Find and click the search button
        search_button = self.driver.find_element(By.CSS_SELECTOR, "button[type='submit']")
        logger.info("Clicking search button")
        search_button.click()
        time.sleep(2)
        logger.info("Clicked search button directly")

    def has_booking(self):
        self.driver.get(f"{BOOKING_URL}?page=profil&view=ma_reservation")
        time.sleep(1)
        self.driver.save_screenshot("reservation_page.png")
        try:
            self.driver.find_element(
                By.CSS_SELECTOR, "button#annuler.btn.btn-darkblue.cancel-button"
            )
            return True
        except NoSuchElementException:
            return False

    def __del__(self):
        """Cleanup method to remove temporary directory and quit driver."""
        if hasattr(self, "driver"):
            try:
                self.driver.quit()
            except:
                pass

        if hasattr(self, "_temp_dir"):
            import shutil

            try:
                shutil.rmtree(self._temp_dir)
            except:
                pass
