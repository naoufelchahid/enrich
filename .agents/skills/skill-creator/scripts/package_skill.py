#!/usr/bin/env python3
"""
Package and validate a skill directory for distribution.
Verifies frontmatter, line limits, relative references, and packages the skill directory.
"""

import os
import sys
import re
import argparse
import shutil
import zipfile

def parse_args():
    parser = argparse.ArgumentParser(description="Package and validate an Antigravity skill directory.")
    parser.add_argument("skill_dir", help="Path to the skill directory (e.g. .gemini/skills/my-skill)")
    parser.add_argument("--output-dir", default=None, help="Directory to save packaged skill file")
    return parser.parse_args()

def validate_skill(skill_dir):
    skill_dir = os.path.abspath(skill_dir)
    skill_md = os.path.join(skill_dir, "SKILL.md")

    if not os.path.exists(skill_md):
        print(f"❌ Error: SKILL.md not found in {skill_dir}")
        return False, None, None

    with open(skill_md, "r", encoding="utf-8") as f:
        content = f.read()

    lines = content.splitlines()
    line_count = len(lines)
    print(f"ℹ️ SKILL.md has {line_count} lines.")
    if line_count > 500:
        print(f"⚠️ Warning: SKILL.md exceeds recommended 500 lines limit ({line_count} lines). Consider moving detailed guidance into references/.")

    # Validate Frontmatter
    frontmatter_match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    if not frontmatter_match:
        print("❌ Error: Valid YAML frontmatter (between --- separators) not found at top of SKILL.md")
        return False, None, None

    fm_text = frontmatter_match.group(1)
    name_match = re.search(r"^name:\s*(.+)$", fm_text, re.MULTILINE)
    desc_match = re.search(r"^description:\s*(.+)$", fm_text, re.MULTILINE)

    if not name_match:
        print("❌ Error: Missing 'name' field in SKILL.md frontmatter.")
        return False, None, None

    if not desc_match:
        print("❌ Error: Missing 'description' field in SKILL.md frontmatter.")
        return False, None, None

    skill_name = name_match.group(1).strip()
    skill_desc = desc_match.group(1).strip()

    print(f"✅ Frontmatter valid for skill '{skill_name}'.")

    # Validate local references
    ref_links = re.findall(r"\[.*?\]\((?!http)(.*?)\)", content)
    for link in ref_links:
        clean_link = link.split("#")[0]
        if clean_link:
            target_path = os.path.join(skill_dir, clean_link)
            if not os.path.exists(target_path):
                print(f"⚠️ Warning: Referenced file not found: {clean_link}")

    return True, skill_name, skill_desc

def package_skill(skill_dir, output_dir=None):
    valid, skill_name, desc = validate_skill(skill_dir)
    if not valid:
        sys.exit(1)

    skill_dir = os.path.abspath(skill_dir)
    parent_dir = os.path.dirname(skill_dir)
    out_dir = os.path.abspath(output_dir) if output_dir else parent_dir
    os.makedirs(out_dir, exist_ok=True)

    archive_name = os.path.join(out_dir, f"{skill_name}.skill")
    
    # Create zip file with .skill extension
    with zipfile.ZipFile(archive_name, "w", zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(skill_dir):
            for file in files:
                abs_path = os.path.join(root, file)
                rel_path = os.path.relpath(abs_path, skill_dir)
                zipf.write(abs_path, arcname=os.path.join(skill_name, rel_path))

    print(f"🎉 Skill package created successfully: {archive_name}")
    return archive_name

def main():
    args = parse_args()
    package_skill(args.skill_dir, args.output_dir)

if __name__ == "__main__":
    main()
