import streamlit as st
import json
import re
import io
import base64
from datetime import datetime, date

from google.oauth2 import service_account
from google.cloud import documentai_v1 as documentai
import google.auth.transport.requests

from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.units import inch, mm

# --- Google Cloud Project and Processor Settings ---
PROCESSOR_ID = "eea8726131138170"
PROCESSOR_LOCATION = "us"
PROJECT_ID = "ridd-compliance-audit-tool"
PROCESSOR_NAME = f"projects/{PROJECT_ID}/locations/{PROCESSOR_LOCATION}/processors/{PROCESSOR_ID}"
API_ENDPOINT = f"{PROCESSOR_LOCATION}-documentai.googleapis.com"

# --- Authentication ---
# Load credentials from Streamlit secrets
credentials_info = st.secrets["service_account"]
credentials = service_account.Credentials.from_service_account_info(credentials_info)


# --- Load Product Data ---
@st.cache_data
def load_products():
    with open('products.json', 'r', encoding='utf-8') as f:
        return json.load(f)

products = load_products()

# --- Helper Functions ---
def process_with_docai(file_bytes):
    # Initialize the Document AI client
    client = documentai.DocumentProcessorServiceClient(
        client_options={"api_endpoint": API_ENDPOINT},
        credentials=credentials
    )

    # Load Binary Data into Document AI RawDocument Object
    raw_document = documentai.RawDocument(
        content=file_bytes,
        mime_type="application/pdf"
    )

    # Configure the process request
    request = documentai.ProcessRequest(
        name=PROCESSOR_NAME,
        raw_document=raw_document
    )

    # Process the document
    result = client.process_document(request=request)

    # Extract text
    document = result.document
    text = document.text

    # Extract entities if available
    extracted_entities = {}
    for entity in document.entities:
        key = entity.type_
        value = entity.mention_text
        extracted_entities[key] = value

    return text, extracted_entities

def normalize_text(text):
    if isinstance(text, str):
        return ' '.join(text.lower().split())
    return ''

def extract_usage_rate(text, product_name):
    patterns = [
        r"Concentrated Amount:\s*([\d.]+)\s*(grams?|g|gallons?|gal|cups?|c|ounces?|oz|fl\s*oz)",
        r"Application Rate:\s*([\d.]+)\s*(grams?|g|gallons?|gal|cups?|c|ounces?|oz|fl\s*oz)",
        r"([\d.]+)\s*(grams?|g|gallons?|gal|cups?|c|ounces?|oz|fl\s*oz)\s*per\s*([\d.]+)\s*(square\s*feet|sq\s*ft|sqft|cubic\s*feet|cu\s*ft)"
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            value = match.group(1)
            unit = match.group(2)
            extracted_rate = f"{value} {unit}"
            return extracted_rate
    return "Not found"

def check_compliance(extracted_text, products):
    compliance_results = []
    normalized_text = normalize_text(extracted_text)
    for product in products:
        if not isinstance(product, dict):
            st.warning(f"Invalid product entry: {product}")
            continue
        try:
            normalized_product_name = normalize_text(product['Product'])
        except Exception as e:
            continue

        if isinstance(normalized_product_name, str) and normalized_product_name in normalized_text:
            details = []
            compliant = True

            labeled_usage_rates = []
            actual_usage_rate = extract_usage_rate(extracted_text, product['Product'])

            # Check application rates dynamically
            application_rates = product.get('Application Rates', {})
            if isinstance(application_rates, dict):
                for rate_type, rate in application_rates.items():
                    if isinstance(rate, dict):
                        for sub_rate_type, sub_rate in rate.items():
                            labeled_usage_rates.append(sub_rate)
                            if isinstance(sub_rate, str) and normalize_text(sub_rate) not in normalized_text:
                                compliant = False
                                details.append(f"Missing application rate for {sub_rate_type}: {sub_rate}")
                    else:
                        labeled_usage_rates.append(rate)
                        if isinstance(rate, str) and normalize_text(rate) not in normalized_text:
                            compliant = False
                            details.append(f"Missing application rate for {rate_type}: {rate}")
            elif isinstance(application_rates, str):
                labeled_usage_rates.append(application_rates)
                if normalize_text(application_rates) not in normalized_text:
                    compliant = False
                    details.append(f"Missing application rate: {application_rates}")

            # Check max application rate
            max_rate = product.get('Max Application Rate')
            deviation = f"Actual: {actual_usage_rate}, Labeled: {max_rate}"
            if max_rate:
                if isinstance(max_rate, str) and normalize_text(max_rate) not in normalized_text:
                    compliant = False
                    details.append(f"Exceeded max application rate: {max_rate}, {deviation}")
                else:
                    details.append(f"Max application rate within limit: {max_rate}, {deviation}")

            # Check conditions dynamically
            conditions = product.get('Conditions', {})
            if isinstance(conditions, dict):
                for condition, required in conditions.items():
                    if required and isinstance(condition, str) and normalize_text(condition) not in normalized_text:
                        compliant = False
                        details.append(f"Condition not met: {condition}")

            # Check additional rules dynamically
            additional_rules = product.get('Additional Rules', [])
            if isinstance(additional_rules, list):
                for rule in additional_rules:
                    rule_text = rule.get('rule_text', '')
                    if isinstance(rule_text, str) and normalize_text(rule_text) not in normalized_text:
                        compliant = False
                        details.append(f"Rule not met: {rule['description']}")

            compliance_results.append({
                'product': product['Product'],
                'compliant': compliant,
                'details': details,
                'actual_usage_rate': actual_usage_rate,
                'labeled_usage_rate': ', '.join(labeled_usage_rates),
                'deviation': deviation
            })
    return compliance_results

def display_results(results):
    for result in results:
        st.header(f"Results for {result['file']}")
        st.write(f"**Technician:** {result['technician']}")
        st.write(f"**Date:** {result['date']}")
        st.subheader("Compliance Results")

        for compliance in result['compliance_results']:
            st.markdown(f"### Product: {compliance['product']}")
            st.write(f"**Compliant:** {'Yes' if compliance['compliant'] else 'No'}")
            st.write("**Details:**")
            for detail in compliance['details']:
                st.write(f"- {detail}")
            st.write(f"**Actual Usage Rate:** {compliance['actual_usage_rate']}")
            st.write(f"**Labeled Usage Rate:** {compliance['labeled_usage_rate']}")
            st.write(f"**Deviation:** {compliance['deviation']}")
            st.write("---")

        # Optionally display the extracted text
        with st.expander(f"Show Extracted Text for {result['file']}"):
            st.text(result['ocr_text'])

def generate_pdf(data):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter)
    styles = getSampleStyleSheet()

    # Custom styles
    custom_style = ParagraphStyle(
        name='CustomStyle',
        parent=styles['BodyText'],
        fontName='Helvetica-Bold',
        fontSize=12,
        leading=14,
        textColor=colors.black,
        spaceAfter=12
    )

    flowables = []

    for result in data:
        # Add a header
        header = Paragraph(f"Compliance Report for {result['file']}", styles['Title'])
        flowables.append(header)
        flowables.append(Spacer(1, 12))

        # Technician and Date
        tech_info = Paragraph(f"Technician: {result['technician']} | Date: {result['date']}", styles['Normal'])
        flowables.append(tech_info)
        flowables.append(Spacer(1, 12))

        # Add Compliance Results
        for compliance in result['compliance_results']:
            product_title = Paragraph(f"Product: {compliance['product']}", styles['Heading2'])
            compliance_status = Paragraph(f"Compliant: {'Yes' if compliance['compliant'] else 'No'}", styles['BodyText'])
            labeled_usage_rate = Paragraph(f"Labeled Usage Rate: {compliance['labeled_usage_rate']}", styles['BodyText'])
            actual_usage_rate = Paragraph(f"Actual Usage Rate: {compliance['actual_usage_rate']}", styles['BodyText'])
            deviation = Paragraph(f"Deviation: {compliance['deviation']}", styles['BodyText'])

            flowables.extend([product_title, compliance_status, labeled_usage_rate, actual_usage_rate, deviation])

            for detail in compliance['details']:
                detail_paragraph = Paragraph(f"- {detail}", custom_style)
                flowables.append(detail_paragraph)
            flowables.append(Spacer(1, 12))

    # Build the PDF
    doc.build(flowables)
    pdf = buffer.getvalue()
    buffer.close()
    return pdf

def add_rule():
    st.sidebar.subheader("Add New Rule")
    product_name = st.sidebar.text_input("Product Name")
    rule_description = st.sidebar.text_input("Rule Description")
    rule_text = st.sidebar.text_area("Rule Text")
    missing_application_rate = st.sidebar.checkbox("Missing Application Rate")

    if st.sidebar.button("Add Rule"):
        # Find or create the product
        product_found = False
        for product in products:
            if product.get('Product') == product_name:
                product_found = True
                if 'Additional Rules' not in product:
                    product['Additional Rules'] = []
                product['Additional Rules'].append({
                    'description': rule_description,
                    'rule_text': rule_text,
                    'missing_application_rate': missing_application_rate
                })
                break

        if not product_found:
            new_product = {
                'Product': product_name,
                'Additional Rules': [{
                    'description': rule_description,
                    'rule_text': rule_text,
                    'missing_application_rate': missing_application_rate
                }]
            }
            products.append(new_product)

        # Save the updated products list
        with open('products.json', 'w', encoding='utf-8') as f:
            json.dump(products, f, indent=4)
        st.sidebar.success("Rule added successfully!")

def main():
    st.title("RIDD Compliance Audit Tool")

    # --- User Input Fields ---
    technician = st.text_input("Technician Name:")
    service_date = st.date_input("Service Date:", value=date.today())

    # Add Rule
    add_rule()

    uploaded_files = st.file_uploader(
        "Upload Service Tickets (PDF)", type=['pdf'], accept_multiple_files=True
    )

    if uploaded_files and technician:
        results = []
        for uploaded_file in uploaded_files:
            file_bytes = uploaded_file.read()
            try:
                extracted_text, _ = process_with_docai(file_bytes)

                compliance_results = check_compliance(extracted_text, products)

                results.append({
                    'file': uploaded_file.name,
                    'technician': technician,
                    'date': service_date.strftime("%Y-%m-%d"),
                    'ocr_text': extracted_text,
                    'compliance_results': compliance_results
                })

            except Exception as e:
                st.error(
                    f"An error occurred processing {uploaded_file.name}: {e}"
                )

        # Display Results
        display_results(results)

        # Generate PDF Report
        if st.button("Generate PDF Report"):
            pdf_bytes = generate_pdf(results)
            st.download_button(label="Download PDF Report",
                               data=pdf_bytes,
                               file_name='compliance_report.pdf',
                               mime='application/pdf')

    elif uploaded_files and not technician:
        st.warning("Please enter a technician name.")

if __name__ == "__main__":
    main()
