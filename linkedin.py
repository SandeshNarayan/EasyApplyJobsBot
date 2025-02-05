from typing import List, Optional
import re

from selenium import webdriver
from selenium.common.exceptions import NoSuchElementException, TimeoutException
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

import config
import constants
import models
import repository_wrapper
import utils.utils as utils
import utils.file as resultFileWriter
import utils.linkedinUrlGenerator as linkedinUrlGenerator
import utils.logger as logger
from utils.logger import MessageTypes
import utils.sleeper as sleeper


# This class is responsible for handling the LinkedIn job application process
# It uses the Selenium WebDriver to interact with the LinkedIn website
# It also uses the repository_wrapper to interact with the backend
#
# The class is responsible for:
# - Logging in to LinkedIn (done in the constructor)
# - Searching for jobs
# - Applying to jobs
# - Handling job posts
# - Handling questions
# - Handling multiple pages of the application process
# - Handling the resume selection
# - Handling the submission of the application
# - Handling the follow company checkbox
# - Handling the application of the job
class Linkedin:
    def __init__(self):
        logger.logDebugMessage("🌐 The Bot is starting.", MessageTypes.WARNING)

        if config.chromeDriverPath != "":
            # Specify the path to Chromedriver provided by the Alpine package
            service = ChromeService(executable_path=config.chromeDriverPath)
        else:
            service = ChromeService(ChromeDriverManager().install())
        
        self.driver = webdriver.Chrome(service=service, options=utils.chromeBrowserOptions())        
        self.wait = WebDriverWait(self.driver, 15)

        # Navigate to the LinkedIn home page to check if we're already logged in
        self.goToUrl("https://www.linkedin.com")

        if not self.checkIfLoggedIn():
            self.goToUrl("https://www.linkedin.com/login?trk=guest_homepage-basic_nav-header-signin")

            logger.logDebugMessage("🔄 Trying to log in linkedin...", MessageTypes.WARNING)
            try:    
                sleeper.interact(lambda : self.driver.find_element("id", "username").send_keys(config.email))
                sleeper.interact(lambda : self.driver.find_element("id", "password").send_keys(config.password))
                sleeper.interact(lambda : self.driver.find_element("xpath",'//button[@type="submit"]').click())
                self.checkIfLoggedIn()
            except Exception as e:
                logger.logDebugMessage("❌ Couldn't log in Linkedin by using Chrome. Please check your Linkedin credentials on config files line 7 and 8. If error continue you can define Chrome profile or run the bot on Firefox", MessageTypes.ERROR, e)
        
        repository_wrapper.init()
    

    def checkIfLoggedIn(self):
        if self.exists(self.driver, By.CSS_SELECTOR, "img.global-nav__me-photo.evi-image.ember-view"):
            logger.logDebugMessage("✅ Logged in Linkedin.", MessageTypes.SUCCESS)
            return True
        else:
            return False


    def startApplying(self):
        try:
            jobCounter = models.JobCounter()

            urlData = linkedinUrlGenerator.generateSearchUrls()

            for url in urlData:        
                self.goToUrl(url)

                urlWords = utils.urlToKeywords(url)
                
                try:
                    totalJobs = self.wait.until(EC.presence_of_element_located((By.XPATH, '//small'))).text # TODO - fix finding total jobs
                    # totalJobs = self.driver.find_element(By.XPATH,'//small').text 

                    totalSearchResultPages = utils.jobsToPages(totalJobs)

                    lineToWrite = "\n Search keyword: " + urlWords[0] + ", Location: " + urlWords[1] + ", Found " + str(totalJobs)
                    resultFileWriter.displayWriteResults(lineToWrite)

                    for searchResultPage in range(totalSearchResultPages):
                        currentSearchResultPageJobs = constants.jobsPerPage * searchResultPage
                        url = url + "&start=" + str(currentSearchResultPageJobs)
                        self.goToUrl(url)

                        jobsForVerification = self.getJobsForVerificationFromSearchPage()
                        verifiedJobs = repository_wrapper.verify_jobs(jobsForVerification)

                        for job in verifiedJobs:
                            jobCounter = self.processJob(jobID=job.linkedinJobId, jobCounter=jobCounter)
                                    
                except TimeoutException:
                    logger.logDebugMessage("0 jobs found for: " + urlWords[0] + " in " + urlWords[1], MessageTypes.ERROR)

                logger.logDebugMessage("Category: " + urlWords[0] + " in " + urlWords[1]+ " applied: " + str(jobCounter.applied) +
                    " jobs out of " + str(jobCounter.total) + ".", MessageTypes.SUCCESS)

        except Exception as e:
            logger.logDebugMessage("Unhandled exception in startApplying", MessageTypes.ERROR, e, True)
            self.driver.save_screenshot("unhandled_exception.png")
            with open("page_source_at_unhandled_exception.html", "w") as file:
                file.write(self.driver.page_source)


    def goToJobsSearchPage(self):
        searchUrl = linkedinUrlGenerator.getGeneralSearchUrl()
        self.goToUrl(searchUrl)


    def goToEasyApplyJobsSearchPage(self):
        searchUrl = linkedinUrlGenerator.getEasyApplySearchUrl()
        self.goToUrl(searchUrl)

    
    def goToUrl(self, url: str):
        sleeper.interact(lambda : self.driver.get(url))
        

    def goToJobPage(self, jobID: str):
        jobPage = 'https://www.linkedin.com/jobs/view/' + jobID
        self.goToUrl(jobPage)
        return jobPage


    def processJob(self, jobID: str, jobCounter: models.JobCounter):
        jobPage = self.goToJobPage(jobID)
        jobCounter.total += 1
        sleeper.sleepInBetweenBatches(jobCounter.total)

        jobProperties = self.getJobPropertiesFromJobPage(jobID)
        repository_wrapper.update_job(jobProperties)
        if self.isJobBlacklisted(company=jobProperties.company, title=jobProperties.title): 
            jobCounter.skipped_blacklisted += 1
            lineToWrite = self.getLogTextForJobProperties(jobProperties, jobCounter) + " | " + "* 🤬 Blacklisted Job, skipped!: " + str(jobPage)
            resultFileWriter.displayWriteResults(lineToWrite)
            return jobCounter

        jobCounter = self.handleJobPost(
            jobPage=jobPage, 
            jobProperties=jobProperties, 
            jobCounter=jobCounter)

        return jobCounter
    
    
    def getJobsForVerificationFromSearchPage(self) -> List[models.JobForVerification]:
        jobsListItems = self.getJobsListFromSearchPage()
        jobsForVerification = []

        for jobItem in jobsListItems:
            if self.exists(jobItem, By.XPATH, constants.appliedTextXPATH):
                logger.logDebugMessage("Not adding a job as already applied", MessageTypes.INFO)
                continue

            jobTitle = self.getJobTitleFromJobCardInSearchResults(jobItem)
            if not jobTitle:
                logger.logDebugMessage("Could not extract job title from job card", MessageTypes.WARNING)
                continue

            if self.isTitleBlacklisted(jobTitle):
                logger.logDebugMessage(f"Not adding job as title '{jobTitle}' is blacklisted", MessageTypes.INFO)
                continue

            companyName = self.getCompanyNameFromJobCardInSearchResults(jobItem)
            if not companyName:
                logger.logDebugMessage("Could not extract company name from job card", MessageTypes.WARNING)
                continue

            if self.isCompanyBlacklisted(companyName):
                logger.logDebugMessage(f"Not adding job as company '{companyName}' is blacklisted", MessageTypes.INFO)
                continue

            jobId = jobItem.get_attribute(constants.jobCardIdAttribute)
            if not jobId:
                logger.logDebugMessage("Could not extract job ID from job card", MessageTypes.WARNING)
                continue

            workPlaceType = self.getWorkplaceTypeFromJobCardInSearchResults(jobItem)

            jobsForVerification.append(models.JobForVerification(
                linkedinJobId=jobId.split(":")[-1],
                title=jobTitle,
                company=companyName,
                workplaceType=workPlaceType))

        return jobsForVerification


    def getCompanyNameFromJobCardInSearchResults(self, jobItem) -> Optional[str]:
        selectors = [
            constants.jobCardCompanyNameCSS,
            constants.jobCardSubtitleCSS, 
            constants.jobCardMetadataCSS,
            constants.jobCardCompanyCSS
        ]
        
        for selector in selectors:
            elements = jobItem.find_elements(By.CSS_SELECTOR, selector)
            if elements and len(elements) > 0:
                return utils.getFirstStringBeforeSeparators(elements[0].text)
            
        return None


    def getJobTitleFromJobCardInSearchResults(self, jobItem) -> Optional[str]:
        if (not self.exists(jobItem, By.CSS_SELECTOR, constants.jobCardTitleLinkCSS)):
            return None
        
        element = jobItem.find_element(By.CSS_SELECTOR, constants.jobCardTitleLinkCSS)
        return element.get_attribute("aria-label").strip()


    def getWorkplaceTypeFromJobCardInSearchResults(self, jobItem) -> str:
        description_spans = jobItem.find_elements(By.CSS_SELECTOR, constants.jobCardDescriptionCSS)
        if description_spans and len(description_spans) > 0:
            text = description_spans[0].text
            workplace_type = utils.extractTextWithinParentheses(text)
            return self.verifyWorkPlaceType(workplace_type)
        return ""


    # TODO Move to logger.py (after splitting utils.py)
    def getLogTextForJobProperties(self, jobProperties: models.Job, jobCounter: models.JobCounter):
        textToWrite = str(jobCounter.total) + " | " + jobProperties.title +  " | " + jobProperties.company +  " | " + jobProperties.location + " | " + jobProperties.workplace_type + " | " + jobProperties.posted_date + " | " + jobProperties.applicants_at_time_of_applying
        if self.isJobBlacklisted(company=jobProperties.company, title=jobProperties.title):
            textToWrite = textToWrite + " | " + "blacklisted"

        return textToWrite
        

    def handleJobPost(self, jobPage, jobProperties: models.Job, jobCounter: models.JobCounter):
        if not self.isEasyApplyButtonDisplayed():
            jobCounter.skipped_already_applied += 1
            lineToWrite = self.getLogTextForJobProperties(jobProperties, jobCounter) + " | " + "* 🥳 Already applied! Job: " + str(jobPage)
            resultFileWriter.displayWriteResults(lineToWrite)
            return jobCounter
        
        self.clickEasyApplyButton()
        
        if not self.isApplicationPopupDisplayed():
            return jobCounter
        
        # Now, the easy apply popup should be open
        if self.isSubmitButtonDisplayed():
            jobCounter = self.handleSubmitPage(jobPage, jobProperties, jobCounter)
        elif self.isNextButtonDisplayed():
            jobCounter = self.handleMultiplePages(jobPage, jobProperties, jobCounter)

        return jobCounter
    

    def chooseResumeIfPossible(self, jobProperties: models.Job):
        if self.isResumePage():
            sleeper.interact(lambda : self.clickIfExists(By.CSS_SELECTOR, "button[aria-label='Show more resumes']"))

            # Find all CV container elements
            cv_containers = self.driver.find_elements(By.CSS_SELECTOR, ".jobs-document-upload-redesign-card__container")

            # Loop through the elements to find the desired CV
            for container in cv_containers:
                cv_name_element = container.find_element(By.CLASS_NAME, "jobs-document-upload-redesign-card__file-name")
                
                if config.distinctCVKeyword[0] in cv_name_element.text:
                    # Check if CV is already selected
                    if 'jobs-document-upload-redesign-card__container--selected' not in container.get_attribute('class'):
                        sleeper.interact(lambda : self.click_button(cv_name_element))

                    # Update the backend to save the selected CV
                    repository_wrapper.attached_resume_to_job(jobProperties, cv_name_element.text)
                    # exit the loop once the desired CV is found and selected
                    break  


    def isResumePage(self):
        upload_button_present = self.exists(self.driver, By.CSS_SELECTOR, "label.jobs-document-upload__upload-button")
        resume_container_present = self.exists(self.driver, By.CSS_SELECTOR, "div.jobs-document-upload-redesign-card__container")
        return upload_button_present and resume_container_present


    def getJobPropertiesFromJobPage(self, jobID: str) -> models.Job: 
        jobTitle = self.getJobTitleFromJobPage()
        jobCompany = self.getJobCompanyFromJobPage()
        jobLocation = ""
        jobPostedDate = ""
        numberOfApplicants = ""
        jobWorkPlaceType = self.getJobWorkPlaceTypeFromJobPage()
        jobDescription = self.getJobDescriptionFromJobPage()

        # First, find the container that holds all the elements.
        if self.exists(self.driver, By.XPATH, "//div[contains(@class, 'job-details-jobs-unified-top-card__primary-description-container')]//div"):
            primary_description_div = self.driver.find_element(By.XPATH, "//div[contains(@class, 'job-details-jobs-unified-top-card__primary-description-container')]//div")
            jobLocation = self.getJobLocationFromJobPage(primary_description_div)
            jobPostedDate = self.getJobPostedDateFromJobPage(primary_description_div)
            numberOfApplicants = self.getNumberOfApplicantsFromJobPage(primary_description_div)
        else:
            logger.logDebugMessage("in getting primary_description_div", MessageTypes.WARNING)

        return models.Job(
            title=jobTitle,
            company=jobCompany,
            location=jobLocation,
            description=jobDescription,
            workplace_type=jobWorkPlaceType,
            posted_date=jobPostedDate,
            applicants_at_time_of_applying=numberOfApplicants,
            linkedin_job_id=jobID
        )
    

    def getJobTitleFromJobPage(self) -> str:
        jobTitle = ""

        try:
            jobTitleElement = self.driver.find_element(By.CSS_SELECTOR, "h1.t-24.t-bold.inline")
            jobTitle = jobTitleElement.text.strip()
        except Exception as e:
            logger.logDebugMessage("in getting jobTitle", MessageTypes.WARNING, e)

        return jobTitle
    

    def getJobCompanyFromJobPage(self) -> str:
        jobCompany = ""

        if self.exists(self.driver, By.XPATH, "//div[contains(@class, 'job-details-jobs-unified-top-card__company-name')]//a"):
            # Inside this container, find the company name link.
            jobCompanyElement = self.driver.find_element(By.XPATH, "//div[contains(@class, 'job-details-jobs-unified-top-card__company-name')]//a")
            jobCompany = jobCompanyElement.text.strip()
            
        else:
            logger.logDebugMessage("in getting jobCompany card", MessageTypes.WARNING)

        return jobCompany        
    
    
    def getJobLocationFromJobPage(self, primary_description_div) -> str:
        jobLocation = ""

        try:
            jobLocationSpan = primary_description_div.find_element(By.XPATH, ".//span[contains(@class, 'tvm__text--low-emphasis')][1]")
            jobLocation = jobLocationSpan.text.strip()
        except Exception as e:
            logger.logDebugMessage("in getting jobLocation", MessageTypes.WARNING, e)

        return jobLocation


    def getJobPostedDateFromJobPage(self, primary_description_div) -> str:
        jobPostedDate = ""

        try:
            primary_description_text = primary_description_div.text  # Get all text from the div
            # Regex pattern to find patterns like '6 hours ago', '2 days ago', etc.
            match = re.search(r'\b\d+\s+(seconds?|minutes?|hours?|days?|weeks?|months?)\s+ago\b', primary_description_text)
            if match:
                jobPostedDate = match.group(0)  # The whole matched text is the date

        except Exception as e:
            logger.logDebugMessage("Error in getting jobPostedDate", MessageTypes.WARNING, e)

        return jobPostedDate


    def getNumberOfApplicantsFromJobPage(self, primary_description_div) -> str:
        jobApplications = ""

        try:
            # Find all spans with the class 'tvm__text--low-emphasis'
            primaryDescriptionSpans = primary_description_div.find_elements(By.XPATH, ".//span[contains(@class, 'tvm__text--low-emphasis')]")
            # Loop through all found spans in reverse order because the number of applicants is usually the last one
            for span in reversed(primaryDescriptionSpans):
                span_text = span.text.strip()
                # Check if the text contains the keyword 'appl' (from 'applicants' or 'applications') and a number 
                if 'appl' in span_text.lower() and any(char.isdigit() for char in span_text):
                    jobApplications = span_text
                    break

        except Exception as e:
            logger.logDebugMessage("in getting jobApplications", MessageTypes.WARNING, e)

        return jobApplications


    def getJobWorkPlaceTypeFromJobPage(self) -> str:
        jobWorkPlaceType = ""

        try:
            jobWorkPlaceTypeElement = self.driver.find_element(By.XPATH, "//li[contains(@class, 'job-details-jobs-unified-top-card__job-insight')]/span/span")
            firstSpanText = jobWorkPlaceTypeElement.text.strip().split('\n')[0]
            jobWorkPlaceType = self.verifyWorkPlaceType(firstSpanText)
        except Exception as e:
            logger.logDebugMessage("in getting jobWorkPlaceType", MessageTypes.WARNING, e)
            
        return jobWorkPlaceType
    

    # TODO Find a faster way to verify workplace type
    def verifyWorkPlaceType(self, text: str) -> str:
        keywords = ["Remote", "On-site", "Hybrid"]
        if any(text in keyword for keyword in keywords):
            return text
        else:
            return ""


    # TODO Use jobDetail later
    def getJobDescriptionFromJobPage(self):
        jobDescription = ""

        try:
            # Directly target the div with the specific ID that contains the job description
            descriptionContainer = self.driver.find_element(By.ID, "job-details")
            jobDescription = descriptionContainer.text  # This should get all text within, including nested spans and divs
        except Exception as e:
            logger.logDebugMessage("in getting jobDescription: ", MessageTypes.WARNING, e)

        return jobDescription
    

    def isJobBlacklisted(self, company: str, title: str):
        is_blacklisted = self.isCompanyBlacklisted(company)
        if is_blacklisted:
            return True

        is_blacklisted = self.isTitleBlacklisted(title)
        if is_blacklisted:
            return True

        return False
    

    def isCompanyBlacklisted(self, company: str):
        return any(blacklistedCompany.strip().lower() == company.lower() for blacklistedCompany in config.blacklistCompanies)
    

    def isTitleBlacklisted(self, title: str):
        return any(blacklistedTitle.strip().lower() in title.lower() for blacklistedTitle in config.blackListTitles)


    def extract_percentage(self):
        if not self.exists(self.driver, By.XPATH, constants.multiplePagePercentageXPATH):
            logger.logDebugMessage("Could not find percentage element", MessageTypes.WARNING)
            return None

        percentageElement = self.driver.find_element(By.XPATH, constants.multiplePagePercentageXPATH)
        comPercentage = percentageElement.get_attribute("value")
        
        if not comPercentage or not comPercentage.replace('.', '').isdigit():
            logger.logDebugMessage(f"Invalid percentage value: {comPercentage}", MessageTypes.ERROR)
            return None

        percentage = float(comPercentage)
        if percentage <= 0:
            logger.logDebugMessage("Percentage must be positive", MessageTypes.ERROR)
            return None
        
        return percentage
    
    def handleMultiplePages(self, jobPage, jobProperties: models.Job, jobCounter: models.JobCounter):
        while True:
            self.clickNextButton()
            if self.isQuestionsUnansweredErrorMessageDisplayed():
                # TODO Change the logic when answering to questions is implemented
                jobCounter = self.cannotApply(jobPage, jobProperties, jobCounter)
                return jobCounter
            self.handleApplicationStep(jobProperties)
            if not self.isNextButtonDisplayed():
                break

        if self.isLastApplicationStepDisplayed():
            self.clickReviewApplicationButton()

        if self.isQuestionsUnansweredErrorMessageDisplayed():
            # TODO Change the logic when answering to questions is implemented
            jobCounter = self.cannotApply(jobPage, jobProperties, jobCounter)
            return jobCounter

        jobCounter = self.handleSubmitPage(jobPage, jobProperties, jobCounter)

        return jobCounter
    

    def cannotApply(self, jobPage, jobProperties: models.Job, jobCounter: models.JobCounter) -> models.JobCounter:
        jobCounter.skipped_unanswered_questions += 1
        # TODO Instead of except, output which questions need to be answered
        lineToWrite = self.getLogTextForJobProperties(jobProperties, jobCounter) + " | " + "* 🥵 Couldn't apply to this job! Extra info needed. Link: " + str(jobPage)
        resultFileWriter.displayWriteResults(lineToWrite)

        return jobCounter
        

    def handleSubmitPage(self, jobPage, jobProperties: models.Job, jobCounter: models.JobCounter):
        followCompany = self.driver.find_element(By.CSS_SELECTOR, constants.followCheckboxCSS)
        # Use JavaScript to check the state of the checkbox
        is_followCompany_checked = self.driver.execute_script("""
            var label = arguments[0];
            var checkbox = document.getElementById('follow-company-checkbox');
            var style = window.getComputedStyle(label, '::after');
            var content = style.getPropertyValue('content');
            // Check if content is not 'none' or empty which may indicate the presence of the ::after pseudo-element
            return checkbox.checked || (content && content !== 'none' && content !== '');
        """, followCompany)
        if config.followCompanies != is_followCompany_checked:
            sleeper.interact(lambda : self.click_button(followCompany))

        if self.isReviewApplicationStepDisplayed():
            self.clickSubmitApplicationButton()
            if self.isApplicationSubmittedDialogDisplayed():
                repository_wrapper.applied_to_job(jobProperties)
                lineToWrite = self.getLogTextForJobProperties(jobProperties, jobCounter) + " | " + "* 🥳 Just Applied to this job: " + str(jobPage)
                resultFileWriter.displayWriteResults(lineToWrite)

                jobCounter.applied += 1

        return jobCounter


    def handleApplicationStep(self, jobProperties: models.Job):
        self.chooseResumeIfPossible(jobProperties)
        # self.handleQuestions(jobProperties)


    def handleQuestions(self, jobProperties: models.Job):
        if self.exists(self.driver, By.CSS_SELECTOR, "div.pb4"):
            # Locate the div that contains all the questions
            questionsContainer = self.driver.find_element(By.CSS_SELECTOR, "div.pb4")

            if self.exists(questionsContainer, By.CSS_SELECTOR, "div.jobs-easy-apply-form-section__grouping"):
                # Find all question groups within that div
                questionGroups = questionsContainer.find_elements(By.CSS_SELECTOR, "div.jobs-easy-apply-form-section__grouping")

                # Iterate through each question group
                for group in questionGroups:
                    # TODO Next commented code is to handle city selection and other dropdowns
                    """  
                    # Find the element (assuming you have a way to locate this div, here I'm using a common class name they might share)
                    div_element = self.driver.find_element(By.CLASS_NAME, "common-class-name")

                    # Check for the specific data-test attribute
                    if div_element.get_attribute("data-test-single-typeahead-entity-form-component") is not None:
                        # Handle the first type of div
                        print("This is the first type of div with data-test-single-typeahead-entity-form-component")

                    elif div_element.get_attribute("data-test-single-line-text-form-component") is not None:
                        # Handle the second type of div
                        print("This is the second type of div with data-test-single-line-text-form-component")

                    else:
                        # Handle the case where the div doesn't match either type
                        print("The div doesn't match either specified type")
                    """

                    if self.exists(group, By.CSS_SELECTOR, "label.artdeco-text-input--label"):
                        # Find the label for the question within the group
                        questionLabel = group.find_element(By.CSS_SELECTOR, "label.artdeco-text-input--label").text
                        
                        # Determine the type of question and call the appropriate handler
                        if self.exists(group, By.CSS_SELECTOR, "input.artdeco-text-input--input"):
                            self.handleTextInput(group, questionLabel, By.CSS_SELECTOR, "input.artdeco-text-input--input")
                        elif self.exists(group, By.CSS_SELECTOR, "textarea"):
                            self.handleTextInput(group, questionLabel, By.CSS_SELECTOR, "textarea")
                        elif self.exists(group, By.CSS_SELECTOR, "input[type='radio']"):
                            self.handleRadioInput(group, questionLabel, By.CSS_SELECTOR, "input[type='radio']")
                        else:
                            self.logUnhandledQuestion(questionLabel)


    def exists(self, parent, by, value):
        # Check if an element exists on the page
        return len(parent.find_elements(by, value)) > 0


    def isEasyApplyButtonDisplayed(self):
        return self.exists(self.driver, By.CSS_SELECTOR, constants.easyApplyButtonCSS)


    def clickEasyApplyButton(self):
        button = self.driver.find_element(By.CSS_SELECTOR, constants.easyApplyButtonCSS)
        sleeper.interact(lambda : self.click_button(button))


    def isApplicationPopupDisplayed(self):
        return self.exists(self.driver, By.XPATH, constants.jobApplicationHeaderXPATH)


    def isNextButtonDisplayed(self):
        return self.exists(self.driver, By.CSS_SELECTOR, constants.nextPageButtonCSS)
    

    def clickNextButton(self):
        button = self.driver.find_element(By.CSS_SELECTOR, constants.nextPageButtonCSS)
        sleeper.interact(lambda : self.click_button(button))
    

    def isLastApplicationStepDisplayed(self):
        return self.exists(self.driver, By.CSS_SELECTOR, constants.reviewApplicationButtonCSS)
    

    def clickReviewApplicationButton(self):
        button = self.driver.find_element(By.CSS_SELECTOR, constants.reviewApplicationButtonCSS)
        sleeper.interact(lambda : self.click_button(button))
    

    def isReviewApplicationStepDisplayed(self):
        return self.exists(self.driver, By.CSS_SELECTOR, constants.submitApplicationButtonCSS)
    

    def isSubmitButtonDisplayed(self):
        return self.exists(self.driver, By.CSS_SELECTOR, constants.submitApplicationButtonCSS)
    

    def clickSubmitApplicationButton(self):
        button = self.driver.find_element(By.CSS_SELECTOR, constants.submitApplicationButtonCSS)
        sleeper.interact(lambda : self.click_button(button))


    def find_jobs_from_search_page(self) -> list[models.JobForVerification]:
        self.goToJobsSearchPage()
        jobs = self.getJobsForVerificationFromSearchPage()
        return jobs


    def isApplicationSubmittedDialogDisplayed(self):
        dialog = self.driver.find_element(By.CSS_SELECTOR, "div[data-test-modal][role='dialog']")
        dismiss_button_present = self.exists(dialog, By.CSS_SELECTOR, "button[aria-label='Dismiss']")
        return dismiss_button_present
    

    def isQuestionsUnansweredErrorMessageDisplayed(self):
        return self.exists(self.driver, By.CSS_SELECTOR, constants.errorMessageForNecessaryFiledCSS)


    def getJobsListFromSearchPage(self):
        return self.driver.find_elements(By.CSS_SELECTOR, constants.jobCardContainerCSS)
    

    def handleTextInput(self, group, questionLabel, by, value):
        # Locate the input element  
        inputElement = group.find_element(by, value)

        # Retrieve the value of the input element
        inputValue = inputElement.get_attribute('value')

        # Check if the input element is empty
        if inputValue == '':
            # TODO Check the backend for answers

            # TODO If there is an answer for this question, fill it in
            # If you want to fill the input
            # question_input.send_keys("Your answer here") then sleep
            # If no answers are found, move to the next step (backend should handle saving unanswered questions)
            if config.displayWarnings:
                logger.logDebugMessage(f"The input for '{questionLabel}' is empty.", MessageTypes.WARNING)
        else:
            # TODO Save answers to the backend if they are not already saved
            if config.displayWarnings:
                logger.logDebugMessage(f"The input for '{questionLabel}' has the following value: {inputValue}", MessageTypes.WARNING)


    def handleRadioInput(self, group, questionLabel, by, value):
        # Check if it's a radio selector question
        radioInputs = group.find_elements(by, value)
        for radioInput in radioInputs:
            # Retrieve the associated label
            label = radioInput.find_element(By.XPATH, "./following-sibling::label").text
            # TODO Check the backend for answers. If there is an answer for this question, fill it in
            # Check or uncheck based on some condition
            # if "desired option" in label:
            #     logger.logDebugMessage(f"Selecting option: {label}", MessageTypes.WARNING)
            #     radio_input.click()  # Select the radio button if it's the desired option then sleep


    def logUnhandledQuestion(self, questionLabel):
        # Log or print the unhandled question
        logger.logDebugMessage(f"Unhandled question: {questionLabel}", MessageTypes.ERROR)


    def clickIfExists(self, by, selector):
        if self.exists(self.driver, by, selector):
            clickableElement = self.driver.find_element(by, selector)
            self.click_button(clickableElement)


    def click_button(self, button):
        try:
            button.click()
        except Exception as e:
            # If click fails, use JavaScript to click on the button
            self.driver.execute_script("arguments[0].click();", button)

