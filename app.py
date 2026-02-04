import os
import tempfile
import re
from datetime import datetime
from flask import Flask, request, render_template, send_file, flash, redirect, url_for, after_this_request
import pdfplumber
from groq import Groq
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import inch
from reportlab.lib.colors import HexColor, black, gray

# Load environment variables for local development
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ================= CONFIGURATION =================
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL = "llama-3.3-70b-versatile"

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "hr-resume-converter-2026")
TEMP_DIR = tempfile.gettempdir()

# ================= HELPERS =================
def extract_text(path):
    """Extract text from PDF or DOCX"""
    text = ""
    try:
        if path.lower().endswith(".pdf"):
            with pdfplumber.open(path) as pdf:
                for page in pdf.pages:
                    text += (page.extract_text() or "") + "\n"
        else:
            # Fallback for DOCX text extraction without python-docx
            import zipfile
            import xml.etree.ElementTree as ET
            with zipfile.ZipFile(path) as docx:
                xml_content = docx.read('word/document.xml')
                tree = ET.fromstring(xml_content)
                namespace = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
                for paragraph in tree.findall('.//w:p', namespace):
                    texts = [node.text for node in paragraph.findall('.//w:t', namespace) if node.text]
                    if texts:
                        text += "".join(texts) + "\n"
    except Exception as e:
        print(f"Extraction Error: {e}")
    return text.strip()

def get_ai_data(resume_text, job_description=""):
    """Get structured data from Groq AI with job description tailoring"""
    if not GROQ_API_KEY:
        print("API Key missing!")
        return None

    # Build dynamic prompt based on whether job description is provided
    jd_context = ""
    if job_description:
        jd_context = f"""\nJOB DESCRIPTION CONTEXT:
{job_description[:1000]}

IMPORTANT: Tailor the resume to match this job description by:
- Highlighting relevant skills and experience
- Using keywords from the job description
- Emphasizing matching qualifications
- Adjusting the professional summary to align with the role
"""

    prompt = f"""Extract and structure professional details from this resume. {jd_context}

STRICT RULES:
1. ONLY use details from the resume text.
2. If information is missing, leave empty.
3. Separate Technical and Soft skills clearly.
4. **Professional Title**: Extract the candidate's primary job role or desired position. Look for:
   - Current job title (e.g., "Senior Java Developer", "Android Developer")
   - If student/fresher, infer from skills (e.g., "Android development" → "Android Developer")
   - Use the most prominent technology or specialization
   - Format: Just the job title, capitalized (e.g., "Android Developer", "Full Stack Developer")
5. **Total Experience**: Look for phrases like "3+ years", "5 years experience", "X years of experience". 
   - For students/freshers with project experience, use phrases like "3 years of project experience" or "Fresher"
   - Calculate if necessary from work history dates
6. **Notice Period**: Look for "Notice Period", "Availability", "can join in", "Immediate joiner". 
   - For freshers/students, use "Immediate" if not mentioned
   - Include details like "(Negotiable)" if present
7. **BOLD FORMATTING**: Wrap all technical terms, technologies, frameworks, tools, and key achievements in **double asterisks** for bold formatting.
   - Examples: **Java**, **Spring Boot**, **MySQL**, **RESTful APIs**, **Git**, **Docker**, **Agile**, **microservices**, etc.
   - Also bold metrics and achievements: **improved performance by 30%**, **reduced costs by 20%**, etc.

**PROFESSIONAL EXPERIENCE RULES:**
- Extract MINIMUM 10 key responsibility points (can be more if available)
- Each point should be a clear, measurable achievement or responsibility
- Use action verbs (Developed, Led, Implemented, Designed, etc.)
- Include metrics where available and **bold them** (e.g., "Improved performance by **30%**")
- Format: Company Name | Role Title | Duration (MMM YYYY - MMM YYYY)
- Then list minimum 10 bullet points of responsibilities/achievements
- **BOLD all technical terms** within each point

**PROJECT EXPERIENCE RULES:**
- For EACH project, structure as follows:
  * Project Name: [Name]
  * Duration: [Start Date - End Date]
  * Technologies: [**Bold** each technology - e.g., **Java**, **Spring Boot**, **MySQL**]
  * Role: [Your specific role]
  * Responsibilities:
    - [Minimum 5 bullet points]
    - **Bold all technical terms** and key achievements
    - Include metrics where available

OUTPUT FORMAT:
CANDIDATE INFORMATION:
- Full Name: [IMPORTANT: Extract from resume header, contact info, or work experience. Look at the very top of resume or in "Name:" field. If found in work experience like "John Doe | Software Engineer", extract "John Doe". NEVER leave as "Not available" or empty - try your best to find it]
- Professional Title:
- Total Experience:
- Notice Period:
- Email:
- Phone:
- Location:

PROFILE SUMMARY:
(2-3 sentence professional summary aligned with job description if provided)
**IMPORTANT**: Bold all technical terms like **Java**, **Spring Boot**, **MySQL**, **3 years of experience**, etc.

PROFESSIONAL EXPERIENCE:
Company Name | Role | Duration
- [Responsibility point 1]
- [Responsibility point 2]
- [Responsibility point 3]
...
- [Responsibility point 10-12]

(Repeat for each job)

PROJECT EXPERIENCE:
Project Name: [Name]
Role: [Role]
Technologies: [Tech stack]
Responsibilities:
- [Responsibility 1]
- [Responsibility 2]
- [Responsibility 3]

(Repeat for each project)

TECHNICAL SKILLS:
(Categorized: Languages, Frameworks, Tools, Databases, etc.)

SOFT SKILLS:
(Leadership, Communication, Problem-solving, etc.)

RESUME TEXT:
{resume_text[:4000]}
"""
    try:
        client = Groq(api_key=GROQ_API_KEY)
        resp = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3 if job_description else 0.2
        )
        return parse_ai_response(resp.choices[0].message.content)
    except Exception as e:
        print(f"AI Error: {e}")
        return None

def parse_ai_response(text):
    """Parse AI output into dictionary"""
    data = {
        "Full Name": "", "Professional Title": "", "Email": "", "Phone": "", "Location": "",
        "Total Experience": "", "Notice Period": "",
        "Profile Summary": "", "Professional Experience": "", "Project Experience": "",
        "Technical Skills": "", "Soft Skills": ""
    }
    
    current_key = None
    for line in text.split("\n"):
        line = line.strip()
        if not line: continue
        
        lower_line = line.lower()
        if "full name:" in lower_line: data["Full Name"] = line.split(":", 1)[1].strip(); continue
        if "professional title:" in lower_line: data["Professional Title"] = line.split(":", 1)[1].strip(); continue
        if "total experience:" in lower_line: data["Total Experience"] = line.split(":", 1)[1].strip(); continue
        if "notice period:" in lower_line: data["Notice Period"] = line.split(":", 1)[1].strip(); continue
        if "email:" in lower_line: data["Email"] = line.split(":", 1)[1].strip(); continue
        if "phone:" in lower_line: data["Phone"] = line.split(":", 1)[1].strip(); continue
        if "location:" in lower_line: data["Location"] = line.split(":", 1)[1].strip(); continue
        
        sec_map = {
            "profile summary:": "Profile Summary",
            "professional experience:": "Professional Experience",
            "project experience:": "Project Experience",
            "technical skills:": "Technical Skills",
            "soft skills:": "Soft Skills"
        }
        for k, v in sec_map.items():
            if k in lower_line:
                current_key = v
                break
        else:
            if current_key:
                data[current_key] += line + "\n"
    
    # Clean up the data
    data = {k: v.strip() for k, v in data.items()}
    
    # Fallback: If name is "Not available" or empty, try to extract from Professional Experience
    if not data["Full Name"] or data["Full Name"].lower() in ["not available", "n/a", "na"]:
        prof_exp = data.get("Professional Experience", "")
        # Look for pattern like "NAME | Role | Duration" or "NAME | Role"
        if prof_exp:
            first_line = prof_exp.split("\n")[0].strip()
            if "|" in first_line:
                potential_name = first_line.split("|")[0].strip()
                # Check if it looks like a name (not too long, not a common phrase)
                if potential_name and len(potential_name.split()) <= 4 and potential_name.lower() not in ["no professional experience", "company name"]:
                    data["Full Name"] = potential_name
    
    return data

def create_resume_pdf(data, path):
    """Generate PDF directly using ReportLab (Cross-Platform)"""
    c = canvas.Canvas(path, pagesize=A4)
    width, height = A4
    margin = 0.5 * inch
    
    # 1. Page Border (Blue)
    c.setStrokeColor(HexColor('#2980B9'))
    c.setLineWidth(1)
    c.rect(margin, margin, width - 2*margin, height - 2*margin)
    
    y = height - margin - 0.4*inch
    
    # 2. Logo (Centered)
    static_dir = os.path.join(os.path.dirname(__file__), 'static')
    logo_path = None
    for ext in ['png', 'jpg', 'jpeg']:
        lp = os.path.join(static_dir, f'krify_logo.{ext}')
        if os.path.exists(lp):
            logo_path = lp
            break
            
    if logo_path:
        logo_w, logo_h = 1.2*inch, 0.6*inch
        c.drawImage(logo_path, (width - logo_w)/2, y - logo_h, width=logo_w, height=logo_h, preserveAspectRatio=True, mask='auto')
        y -= logo_h + 0.3*inch
    else:
        c.setFont("Helvetica-Bold", 18)
        c.setFillColor(HexColor('#003765'))
        c.drawCentredString(width/2, y, "KRIFY")
        y -= 0.5*inch

    # 3. Name & Title (Restored as per Image 2)
    name = data["Full Name"].upper()
    title = re.sub(r'^(PROFESSIONAL TITLE/DESIGNATION:|TITLE:|DESIGNATION:)\s*', '', data["Professional Title"], flags=re.I).strip().upper()
    
    # If no title found, try to infer from profile summary or use a default
    if not title:
        profile = data.get("Profile Summary", "").lower()
        if "android" in profile:
            title = "ANDROID DEVELOPER"
        elif "java" in profile and "spring" in profile:
            title = "JAVA SPRING BOOT DEVELOPER"
        elif "full stack" in profile or "fullstack" in profile:
            title = "FULL STACK DEVELOPER"
        elif "frontend" in profile or "front-end" in profile:
            title = "FRONTEND DEVELOPER"
        elif "backend" in profile or "back-end" in profile:
            title = "BACKEND DEVELOPER"
        elif "web developer" in profile:
            title = "WEB DEVELOPER"
        elif "software" in profile:
            title = "SOFTWARE DEVELOPER"
        else:
            title = "DEVELOPER"  # Generic fallback
    
    # Format: "NAME - ROLE" (e.g., SRUSTI - JAVA SPRING BOOT DEVELOPER)
    display = f"{name} - {title}" if title else name
    
    c.setFont("Helvetica-Bold", 14)
    c.setFillColor(HexColor('#003765'))
    c.drawCentredString(width/2, y, display)
    y -= 0.3*inch

    # 4. Experience & Notice Period ONLY (No Personal Contact Details)
    # Matching Image 2: "3 Years of Experience | Official notice period: 30days"
    parts = []
    
    # We explicitly EXCLUDE Email, Phone, and Location as per user request
    # Only showing Experience and Notice Period
    
    experience = data.get("Total Experience", "").strip()
    notice_period = data.get("Notice Period", "").strip()
    
    # If experience is found, add it
    if experience:
        parts.append(experience)
    else:
        # Check if it's mentioned in profile summary
        profile = data.get("Profile Summary", "").lower()
        if "3 years" in profile or "3+ years" in profile:
            parts.append("3 Years of Experience")
        elif "2 years" in profile or "2+ years" in profile:
            parts.append("2 Years of Experience")
        elif "fresher" in profile or "student" in profile:
            parts.append("Fresher")
    
    # If notice period is found, add it with proper formatting
    if notice_period:
        # Check if it already has "Notice Period:" prefix
        if not notice_period.lower().startswith("notice period"):
            parts.append(f"Official notice period: {notice_period}")
        else:
            parts.append(notice_period)
    else:
        # For freshers/students, assume immediate availability
        if data.get("Profile Summary", "").lower().find("student") != -1 or not data.get("Professional Experience"):
            parts.append("Immediate Joiner")

    if parts:
        c.setFont("Helvetica-Bold", 10) # Using Bold for emphasis since it's the only info
        c.setFillColor(black)
        c.drawCentredString(width/2, y, " | ".join(parts))
        y -= 0.5*inch

    # Helper for adding wrapped sections
    def add_section(title, content, is_project=False):
        nonlocal y
        if not content: return
        
        # INCREASED Space before section (gap between sections)
        y -= 0.45*inch

        # Section Title
        c.setFont("Helvetica-Bold", 12)
        c.setFillColor(HexColor('#003765'))
        c.drawString(margin + 0.2*inch, y, title)
        y -= 0.25*inch
        
        # Section Content (Simple Wrap)
        c.setFont("Helvetica", 10)
        c.setFillColor(HexColor('#34495E'))
        
        lines = content.split('\n')
        for line in lines:
            if y < margin + 1*inch: # New Page
                c.showPage()
                # Re-add border on new page
                c.setStrokeColor(HexColor('#2980B9'))
                c.rect(margin, margin, width-2*margin, height-2*margin)
                y = height - margin - 0.5*inch
                c.setFont("Helvetica", 10)
                c.setFillColor(HexColor('#34495E'))

            line = line.strip()
            if not line: continue
            
            # Check for project structure keywords
            lower_line = line.lower()
            is_meta_line = False
            
            if is_project and any(keyword in lower_line for keyword in ['project name:', 'role:', 'technologies:', 'duration:']):
                # Bold formatting for project metadata
                c.setFont("Helvetica-Bold", 10)
                c.setFillColor(black)
                is_meta_line = True
            elif is_project and 'responsibilities:' in lower_line:
                # Bold for Responsibilities header
                c.setFont("Helvetica-Bold", 10)
                c.setFillColor(black)
                c.drawString(margin + 0.3*inch, y, line)
                y -= 0.25*inch
                continue
            else:
                c.setFont("Helvetica", 10)
                c.setFillColor(HexColor('#34495E'))
            
            # Simple bullet handling
            indent = margin + 0.3*inch
            if line.startswith(('-', '•')):
                c.drawString(indent, y, "•")
                indent += 0.15*inch
                line = line[1:].strip()
            
            # Parse and render text with bold support
            def draw_text_with_bold(text, x_pos, y_pos, max_width):
                """Draw text with **bold** markers properly formatted"""
                nonlocal y
                
                # If no bold markers, use simple rendering
                if '**' not in text:
                    words = text.split()
                    current_line = []
                    for word in words:
                        test_line = " ".join(current_line + [word])
                        if c.stringWidth(test_line, "Helvetica", 10) < max_width:
                            current_line.append(word)
                        else:
                            if current_line:
                                c.setFont("Helvetica", 10)
                                c.drawString(x_pos, y, " ".join(current_line))
                                y -= 0.22*inch
                            current_line = [word]
                            if y < margin + 0.5*inch:
                                return y
                    
                    if current_line:
                        c.setFont("Helvetica", 10)
                        c.drawString(x_pos, y, " ".join(current_line))
                    return y
                
                # Split by bold markers
                parts = re.split(r'(\*\*[^*]+\*\*)', text)
                current_line_parts = []
                
                for part in parts:
                    if not part:
                        continue
                        
                    # Check if this part should be bold
                    is_bold = part.startswith('**') and part.endswith('**') and len(part) > 4
                    actual_text = part[2:-2] if is_bold else part
                    
                    # Set appropriate font and color
                    font = "Helvetica-Bold" if is_bold else "Helvetica"
                    
                    # Word wrap handling
                    words = actual_text.split()
                    for word in words:
                        if not word:
                            continue
                        word_width = c.stringWidth(word + " ", font, 10)
                        line_width = sum([c.stringWidth(w[1], w[0], 10) for w in current_line_parts])
                        
                        if line_width + word_width > max_width and current_line_parts:
                            # Draw current line
                            x = x_pos
                            for font_name, text_part in current_line_parts:
                                c.setFont(font_name, 10)
                                # Use Black for Bold, Dark Gray for Regular
                                if font_name == "Helvetica-Bold":
                                    c.setFillColor(black)
                                else:
                                    c.setFillColor(HexColor('#34495E'))
                                    
                                c.drawString(x, y, text_part)
                                x += c.stringWidth(text_part, font_name, 10)
                            
                            y -= 0.22*inch
                            current_line_parts = []
                            
                            if y < margin + 0.5*inch:
                                return y
                        
                        current_line_parts.append((font, word + " "))
                
                # Draw remaining parts
                if current_line_parts:
                    x = x_pos
                    for font_name, text_part in current_line_parts:
                        c.setFont(font_name, 10)
                        # Use Black for Bold, Dark Gray for Regular
                        if font_name == "Helvetica-Bold":
                            c.setFillColor(black)
                        else:
                            c.setFillColor(HexColor('#34495E'))
                            
                        c.drawString(x, y, text_part)
                        x += c.stringWidth(text_part, font_name, 10)
                
                return y
            
            # Draw the line with bold support
            if is_meta_line:
                # For metadata lines, draw as-is with bold
                c.drawString(indent, y, line.replace('**', ''))
                y -= 0.25*inch
            else:
                # For content, parse bold markers
                try:
                    y = draw_text_with_bold(line, indent, y, width - margin*2 - 0.6*inch)
                    y -= 0.3*inch  # Space between paragraphs
                except Exception as e:
                    # Fallback to simple rendering if bold parsing fails
                    c.setFont("Helvetica", 10)
                    c.drawString(indent, y, line.replace('**', ''))
                    y -= 0.3*inch

    add_section("PROFILE SUMMARY", data["Profile Summary"])
    add_section("PROFESSIONAL EXPERIENCE", data["Professional Experience"])
    add_section("PROJECT EXPERIENCE", data["Project Experience"], is_project=True)
    add_section("TECHNICAL SKILLS", data["Technical Skills"])
    add_section("SOFT SKILLS", data["Soft Skills"])

    # 5. Footer (Left aligned)
    c.setFont("Helvetica-Bold", 8)
    c.setFillColor(black)
    c.drawString(margin + 0.2*inch, margin + 0.4*inch, "Private and Confidential Document – Intended for authorized organizations only.")
    c.setFont("Helvetica", 8)
    c.drawString(margin + 0.2*inch, margin + 0.25*inch, "info@krify.com")

    c.save()
    return path

@app.route("/")
def index(): return render_template("hr_converter.html")

@app.route("/convert", methods=["POST"])
def convert_route():
    # Check API key first
    if not GROQ_API_KEY or GROQ_API_KEY == "your-groq-api-key-here":
        flash("⚠️ GROQ_API_KEY not configured! Please add your API key to the .env file.")
        return redirect("/")
    
    file = request.files.get("candidate_resume")
    if not file: return redirect("/")
    
    # Get job description from form
    job_description = request.form.get("job_description", "").strip()
    
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    in_path = os.path.join(TEMP_DIR, f"in_{ts}_{file.filename}")
    file.save(in_path)
    
    cleanup_list = [in_path]
    try:
        txt = extract_text(in_path)
        data = get_ai_data(txt, job_description)
        if not data:
            flash("❌ Failed to process resume. Please check your API key and try again.")
            return redirect("/")
        
        safe_name = re.sub(r'[^a-zA-Z0-9]', '_', data["Full Name"])[:30] or "Candidate"
        # Remove leading/trailing underscores from safe_name
        safe_name = safe_name.strip('_')
        out_path = os.path.join(TEMP_DIR, f"Resume_{safe_name}_{ts}.pdf")
        final_path = create_resume_pdf(data, out_path)
        
        cleanup_list.append(final_path)
        
        @after_this_request
        def cleanup(resp):
            # Add cache headers to prevent issues
            resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            resp.headers["Pragma"] = "no-cache"
            resp.headers["Expires"] = "0"
            # Content-Disposition is handled by send_file below
            
            for f in cleanup_list:
                if os.path.exists(f): 
                    try: os.remove(f)
                    except: pass
            return resp
            
        return send_file(final_path, as_attachment=True, download_name=os.path.basename(final_path))
    except Exception as e:
        print(f"Route Error: {e}")
        return redirect("/")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5003))
    app.run(host="0.0.0.0", port=port)
