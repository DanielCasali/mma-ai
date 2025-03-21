import streamlit as st
import psycopg2
import psycopg2.extras
import os
from typing import List, Dict, Any, Tuple
import json
import pandas as pd
import time
import httpx
import asyncio
import re


# Set page config at the top level before any other Streamlit commands
st.set_page_config(
    page_title="SQL Assistant (Using Power MMA)",
    page_icon="🤖",
    layout="wide"
)

class DatabaseAnalyzer:
    """Class to analyze PostgreSQL database schema and execute queries."""

    def __init__(self, dbname: str, user: str, password: str, host: str = "localhost", port: str = "5432"):
        """Initialize with database connection parameters."""
        self.connection_params = {
            "dbname": dbname,
            "user": user,
            "password": password,
            "host": host,
            "port": port
        }
        self.connection = None
        self.schema_info = {}
        self.column_semantics = {}  # Store inferred meanings of column names

    def connect(self) -> Tuple[bool, str]:
        """Establish connection to the database."""
        try:
            self.connection = psycopg2.connect(**self.connection_params)
            return True, "Connected to PostgreSQL database successfully!"
        except Exception as e:
            return False, f"Error connecting to PostgreSQL database: {e}"

    def close(self) -> str:
        """Close the database connection."""
        if self.connection:
            self.connection.close()
            return "Database connection closed."

    def get_tables(self) -> List[str]:
        """Get all table names in the database."""
        if not self.connection:
            self.connect()

        cursor = self.connection.cursor()
        cursor.execute("""
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
        """)
        tables = [row[0] for row in cursor.fetchall()]
        cursor.close()
        return tables

    def get_table_columns(self, table_name: str) -> List[Dict[str, str]]:
        """Get column details for a specific table."""
        if not self.connection:
            self.connect()

        cursor = self.connection.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cursor.execute("""
            SELECT column_name, data_type, is_nullable, column_default,
                   character_maximum_length, numeric_precision, numeric_scale
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = %s
        """, (table_name,))

        columns = []
        for row in cursor.fetchall():
            columns.append({
                "name": row["column_name"],
                "type": row["data_type"],
                "nullable": row["is_nullable"],
                "default": row["column_default"],
                "max_length": row["character_maximum_length"],
                "precision": row["numeric_precision"],
                "scale": row["numeric_scale"]
            })

        cursor.close()
        return columns

    def get_foreign_keys(self) -> List[Dict[str, str]]:
        """Get all foreign key relationships in the database."""
        if not self.connection:
            self.connect()

        cursor = self.connection.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cursor.execute("""
            SELECT
                tc.table_name AS table_name,
                kcu.column_name AS column_name,
                ccu.table_name AS foreign_table_name,
                ccu.column_name AS foreign_column_name
            FROM
                information_schema.table_constraints AS tc
                JOIN information_schema.key_column_usage AS kcu
                    ON tc.constraint_name = kcu.constraint_name
                JOIN information_schema.constraint_column_usage AS ccu
                    ON ccu.constraint_name = tc.constraint_name
            WHERE tc.constraint_type = 'FOREIGN KEY'
        """)

        foreign_keys = []
        for row in cursor.fetchall():
            foreign_keys.append({
                "table": row["table_name"],
                "column": row["column_name"],
                "foreign_table": row["foreign_table_name"],
                "foreign_column": row["foreign_column_name"]
            })

        cursor.close()
        return foreign_keys

    def get_primary_keys(self) -> Dict[str, List[str]]:
        """Get primary key columns for each table."""
        if not self.connection:
            self.connect()

        cursor = self.connection.cursor()
        cursor.execute("""
            SELECT
                tc.table_name,
                kcu.column_name
            FROM
                information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                    ON tc.constraint_name = kcu.constraint_name
            WHERE
                tc.constraint_type = 'PRIMARY KEY'
                AND tc.table_schema = 'public'
            ORDER BY tc.table_name, kcu.ordinal_position
        """)

        primary_keys = {}
        for table_name, column_name in cursor.fetchall():
            if table_name not in primary_keys:
                primary_keys[table_name] = []
            primary_keys[table_name].append(column_name)

        cursor.close()
        return primary_keys

    def get_comment_for_column(self, table_name: str, column_name: str) -> str:
        """Get the comment (if any) for a specific column."""
        if not self.connection:
            self.connect()

        cursor = self.connection.cursor()
        cursor.execute("""
            SELECT pg_description.description
            FROM pg_description
            JOIN pg_class ON pg_description.objoid = pg_class.oid
            JOIN pg_attribute ON pg_attribute.attrelid = pg_class.oid
                             AND pg_description.objsubid = pg_attribute.attnum
            WHERE pg_class.relname = %s AND pg_attribute.attname = %s
        """, (table_name, column_name))

        result = cursor.fetchone()
        cursor.close()

        return result[0] if result and result[0] else ""

    def get_comment_for_table(self, table_name: str) -> str:
        """Get the comment (if any) for a specific table."""
        if not self.connection:
            self.connect()

        cursor = self.connection.cursor()
        cursor.execute("""
            SELECT pg_description.description
            FROM pg_description
            JOIN pg_class ON pg_description.objoid = pg_class.oid
            WHERE pg_class.relname = %s AND pg_description.objsubid = 0
        """, (table_name,))

        result = cursor.fetchone()
        cursor.close()

        return result[0] if result and result[0] else ""

    def infer_column_semantics(self, table_name: str, column_name: str, data_type: str) -> str:
        """
        Infer the semantic meaning of a column based on its name and type.
        This is a heuristic-based approach to understand column purposes.
        """
        column_key = f"{table_name}.{column_name}"

        # Check if we already analyzed this column
        if column_key in self.column_semantics:
            return self.column_semantics[column_key]

        # Try to get official comment from database
        comment = self.get_comment_for_column(table_name, column_name)
        if comment:
            self.column_semantics[column_key] = comment
            return comment

        # Common naming patterns for date/time fields
        date_created_patterns = ['created_at', 'creation_date', 'date_created', 'created_on']
        date_updated_patterns = ['updated_at', 'modification_date', 'date_updated', 'modified_on', 'last_updated']
        date_deleted_patterns = ['deleted_at', 'deletion_date', 'date_deleted', 'removed_on']

        # Common patterns for specific types of dates
        delivery_patterns = ['delivery_date', 'delivered_at', 'date_delivered']
        dispatch_patterns = ['dispatch_date', 'dispatched_at', 'date_dispatched', 'shipped_date', 'shipped_at']
        order_patterns = ['order_date', 'ordered_at', 'date_ordered']
        payment_patterns = ['payment_date', 'paid_at', 'date_paid']

        # Id patterns
        id_patterns = ['_id', 'id', '_key', 'uuid', 'guid']

        # Status patterns
        status_patterns = ['status', 'state', 'condition']

        # Lowercased for case-insensitive matching
        col_lower = column_name.lower()

        # Infer meanings based on patterns
        meaning = ""

        # Date semantics
        if data_type in ('date', 'timestamp', 'timestamptz', 'datetime'):
            if any(pattern in col_lower for pattern in date_created_patterns):
                meaning = "Timestamp when the record was created"
            elif any(pattern in col_lower for pattern in date_updated_patterns):
                meaning = "Timestamp when the record was last updated"
            elif any(pattern in col_lower for pattern in date_deleted_patterns):
                meaning = "Timestamp when the record was deleted (for soft deletes)"
            elif any(pattern in col_lower for pattern in delivery_patterns):
                meaning = "Date when items were actually delivered to the customer"
            elif any(pattern in col_lower for pattern in dispatch_patterns):
                meaning = "Date when items were dispatched from the warehouse/facility"
            elif any(pattern in col_lower for pattern in order_patterns):
                meaning = "Date when the order was placed"
            elif any(pattern in col_lower for pattern in payment_patterns):
                meaning = "Date when payment was received"
            elif 'due' in col_lower:
                meaning = "Due date or deadline"
            elif 'start' in col_lower:
                meaning = "Start date of an event or period"
            elif 'end' in col_lower:
                meaning = "End date of an event or period"

        # Numeric semantics
        elif data_type in ('integer', 'bigint', 'numeric', 'decimal', 'double precision'):
            if 'price' in col_lower or 'cost' in col_lower or 'amount' in col_lower:
                meaning = "Monetary value"
            elif 'quantity' in col_lower or 'count' in col_lower or 'number' in col_lower:
                meaning = "Quantity or count"
            elif 'discount' in col_lower:
                meaning = "Discount value (possibly percentage if between 0-1 or 0-100)"
            elif 'total' in col_lower:
                meaning = "Total calculated value"
            elif any(col_lower.endswith(suffix) for suffix in id_patterns):
                meaning = "Identifier or foreign key"

        # Boolean semantics
        elif data_type == 'boolean':
            if 'is_' in col_lower or 'has_' in col_lower:
                attribute = col_lower.replace('is_', '').replace('has_', '')
                meaning = f"Indicates if the record {attribute}"
            elif 'active' in col_lower or 'enabled' in col_lower:
                meaning = "Indicates if the record is active or enabled"

        # String semantics
        elif data_type in ('character varying', 'varchar', 'text', 'char'):
            if any(pattern in col_lower for pattern in status_patterns):
                meaning = "Status or state of the record"
            elif 'name' in col_lower:
                meaning = "Name or title"
            elif 'description' in col_lower or 'desc' in col_lower:
                meaning = "Descriptive text"
            elif 'code' in col_lower:
                meaning = "Code or identifier"
            elif 'address' in col_lower:
                meaning = "Address information"
            elif 'email' in col_lower:
                meaning = "Email address"
            elif 'phone' in col_lower:
                meaning = "Phone number"

        # If no specific meaning was inferred
        if not meaning:
            if any(col_lower.endswith(suffix) for suffix in id_patterns):
                if col_lower == 'id' or col_lower.endswith('_id'):
                    relation = col_lower.replace('_id', '')
                    if relation:
                        meaning = f"Identifier for a {relation}"
                    else:
                        meaning = "Primary identifier"

        # Save inferred meaning
        self.column_semantics[column_key] = meaning
        return meaning

    def get_sample_data(self, table_name: str, limit: int = 3) -> List[Dict[str, Any]]:
        """Get sample data from a table."""
        if not self.connection:
            self.connect()

        cursor = self.connection.cursor(cursor_factory=psycopg2.extras.DictCursor)
        try:
            cursor.execute(f"SELECT * FROM {table_name} LIMIT {limit}")
            columns = [desc[0] for desc in cursor.description]

            results = []
            for row in cursor.fetchall():
                result_dict = {}
                for i, column in enumerate(columns):
                    result_dict[column] = row[i]
                results.append(result_dict)

            return results
        except Exception as e:
            print(f"Error getting sample data: {e}")
            return []
        finally:
            cursor.close()

    def analyze_schema(self) -> Dict[str, Any]:
        """Analyze the database schema and store the information."""
        tables = self.get_tables()
        foreign_keys = self.get_foreign_keys()
        primary_keys = self.get_primary_keys()

        schema_info = {
            "tables": {},
            "relationships": [],
            "primary_keys": primary_keys,
            "sample_data": {},
            "column_semantics": {}
        }

        # Get information about each table and its columns
        for table in tables:
            columns = self.get_table_columns(table)

            # Get table comment
            table_comment = self.get_comment_for_table(table)

            # Process each column and add semantics
            processed_columns = []
            for column in columns:
                # Infer semantics for this column
                semantics = self.infer_column_semantics(table, column["name"], column["type"])

                # Add semantics to column info
                column_with_semantics = column.copy()
                if semantics:
                    column_with_semantics["semantics"] = semantics
                    schema_info["column_semantics"][f"{table}.{column['name']}"] = semantics

                processed_columns.append(column_with_semantics)

            # Store table with its columns and comments
            schema_info["tables"][table] = {
                "columns": processed_columns,
                "comment": table_comment if table_comment else ""
            }

            # Get sample data
            sample_data = self.get_sample_data(table)
            if sample_data:
                schema_info["sample_data"][table] = sample_data

        # Add relationship information
        for fk in foreign_keys:
            schema_info["relationships"].append({
                "table": fk["table"],
                "column": fk["column"],
                "references_table": fk["foreign_table"],
                "references_column": fk["foreign_column"]
            })

        self.schema_info = schema_info
        self.column_semantics = schema_info["column_semantics"]
        return schema_info

    def generate_schema_description(self) -> str:
        """Generate a human-readable description of the database schema."""
        if not self.schema_info:
            self.analyze_schema()

        description = "Database Schema:\n\n"

        # Describe each table and its columns
        for table_name, table_info in self.schema_info["tables"].items():
            description += f"Table: {table_name}"

            # Add table comment if available
            if table_info.get("comment"):
                description += f" - {table_info['comment']}"
            description += "\n"

            description += "Columns:\n"

            for column in table_info["columns"]:
                nullable = "NULL" if column["nullable"] == "YES" else "NOT NULL"
                default = f", DEFAULT: {column['default']}" if column.get("default") else ""

                description += f"  - {column['name']} ({column['type']}, {nullable}{default})"

                # Add semantics if available
                if "semantics" in column and column["semantics"]:
                    description += f" - {column['semantics']}"

                description += "\n"

            # Add primary key information
            if table_name in self.schema_info.get("primary_keys", {}):
                pk_columns = self.schema_info["primary_keys"][table_name]
                description += f"  Primary Key: {', '.join(pk_columns)}\n"

            # Add sample data if available
            if table_name in self.schema_info.get("sample_data", {}):
                description += "Sample data:\n"
                for i, row in enumerate(self.schema_info["sample_data"][table_name][:2]):
                    description += f"  Row {i+1}: {row}\n"

            description += "\n"

        # Describe relationships
        if self.schema_info["relationships"]:
            description += "Relationships:\n"
            for rel in self.schema_info["relationships"]:
                description += f"  - {rel['table']}.{rel['column']} references {rel['references_table']}.{rel['references_column']}\n"

                # Add semantics for the relationship if available
                fk_semantic = self.schema_info["column_semantics"].get(f"{rel['table']}.{rel['column']}", "")
                pk_semantic = self.schema_info["column_semantics"].get(f"{rel['references_table']}.{rel['references_column']}", "")

                if fk_semantic or pk_semantic:
                    description += f"    (Connects "
                    if fk_semantic:
                        description += f"{fk_semantic} "
                    description += f"to "
                    if pk_semantic:
                        description += f"{pk_semantic}"
                    else:
                        description += f"{rel['references_table']}"
                    description += ")\n"

        return description

    def generate_schema_for_llm(self) -> str:
        """Generate a comprehensive schema description for the LLM."""
        if not self.schema_info:
            self.analyze_schema()

        schema_text = "# Database Schema\n\n"

        # Add overall guidance for the model
        schema_text += """
When interpreting column names and generating SQL queries, please note:
- Date columns with different names usually have specific meanings (e.g., order_date vs. delivery_date vs. dispatch_date)
- Primary keys are used to uniquely identify records
- Foreign keys represent relationships between tables
- Column semantics describe the intended meaning and use of each column
- Respect data types when comparing or filtering values
- Use appropriate joins based on the relationships defined below

"""

        # Describe each table and its columns
        for table_name, table_info in self.schema_info["tables"].items():
            schema_text += f"## Table: {table_name}"

            # Add table comment if available
            if table_info.get("comment"):
                schema_text += f" - {table_info['comment']}"
            schema_text += "\n\n"

            # Create a markdown table for columns with semantics
            schema_text += "| Column Name | Data Type | Nullable | Default | Primary Key | Meaning |\n"
            schema_text += "|------------|-----------|----------|---------|-------------|--------|\n"

            # Check if this table has a primary key
            pk_columns = self.schema_info["primary_keys"].get(table_name, [])

            for column in table_info["columns"]:
                nullable = "YES" if column["nullable"] == "YES" else "NO"
                default = column.get("default", "")
                is_pk = "YES" if column["name"] in pk_columns else "NO"
                semantics = column.get("semantics", "")

                schema_text += f"| {column['name']} | {column['type']} | {nullable} | {default} | {is_pk} | {semantics} |\n"

            # Add sample data if available
            if table_name in self.schema_info.get("sample_data", {}) and self.schema_info["sample_data"][table_name]:
                schema_text += "\nSample data:\n```\n"
                sample_data = self.schema_info["sample_data"][table_name]
                if sample_data:
                    keys = sample_data[0].keys()
                    schema_text += ", ".join(keys) + "\n"
                    for row in sample_data[:2]:  # Just show first 2 rows
                        schema_text += ", ".join(str(row[k]) for k in keys) + "\n"
                schema_text += "```\n"

            schema_text += "\n"

        # Describe relationships with semantics
        if self.schema_info["relationships"]:
            schema_text += "## Relationships\n\n"

            for rel in self.schema_info["relationships"]:
                schema_text += f"- {rel['table']}.{rel['column']} → {rel['references_table']}.{rel['references_column']}"

                # Add semantics for the relationship
                fk_semantic = self.schema_info["column_semantics"].get(f"{rel['table']}.{rel['column']}", "")
                pk_semantic = self.schema_info["column_semantics"].get(f"{rel['references_table']}.{rel['references_column']}", "")

                if fk_semantic or pk_semantic:
                    schema_text += f" (Connects "
                    if fk_semantic:
                        schema_text += f"{fk_semantic} "
                    schema_text += f"to "
                    if pk_semantic:
                        schema_text += f"{pk_semantic}"
                    else:
                        schema_text += f"{rel['references_table']}"
                    schema_text += ")"

                schema_text += "\n"

        # Add a list of date-related columns with their meanings for easy reference
        date_columns = {}
        for table, table_info in self.schema_info["tables"].items():
            for column in table_info["columns"]:
                if column["type"] in ('date', 'timestamp', 'timestamptz', 'datetime'):
                    semantic = column.get("semantics", "")
                    if semantic:
                        date_columns[f"{table}.{column['name']}"] = semantic

        if date_columns:
            schema_text += "\n## Date/Time Column Reference\n\n"
            schema_text += "| Column | Meaning |\n"
            schema_text += "|--------|--------|\n"

            for col, meaning in date_columns.items():
                schema_text += f"| {col} | {meaning} |\n"

        return schema_text

    def execute_query(self, query: str) -> Tuple[List[Dict[str, Any]], List[str]]:
        """Execute an SQL query and return the results as a list of dictionaries."""
        if not self.connection:
            self.connect()

        cursor = self.connection.cursor(cursor_factory=psycopg2.extras.DictCursor)
        try:
            cursor.execute(query)

            # Get column names
            columns = [desc[0] for desc in cursor.description] if cursor.description else []

            # Fetch all results
            results = []
            for row in cursor.fetchall():
                result_dict = {}
                for i, column in enumerate(columns):
                    result_dict[column] = row[i]
                results.append(result_dict)

            return results, columns
        except Exception as e:
            raise Exception(f"Error executing query: {e}")
        finally:
            cursor.close()

def extract_sql_from_response(response: str) -> str:
    """
    Extract SQL query from the LLM response, focusing on the content inside triple backticks.
    If triple backticks are present, extract the content within them.
    Otherwise, use the entire response with minimal cleaning.
    """
    # Look for SQL code blocks with triple backticks
    # The pattern matches ```sql ... ``` or just ``` ... ```
    sql_block_pattern = r'```(?:sql)?(.*?)```'
    matches = re.findall(sql_block_pattern, response, re.DOTALL)

    if matches:
        # Use the first code block found
        sql_query = matches[0].strip()
    else:
        # If no code blocks, use the whole response
        sql_query = response.strip()

    # Remove any "SQL query:" prefix or similar text
    sql_query = re.sub(r'^(?:SQL query|Query|SQL):?\s*', '', sql_query, flags=re.IGNORECASE)

    # Remove any lingering backticks
    sql_query = sql_query.replace('`', '')

    return sql_query

class LlamaInterface:
    """Interface for LLM-based natural language to SQL conversion using LLM Runtime API."""

    def __init__(self, host="llama-service", port="8080"):
        """Initialize the LLM Runtime interface with host and port."""
        self.host = host
        self.port = port

    async def get_llama_response(self, prompt):
        """Get a response from the LLM Runtime API."""
        json_data = {
            'prompt': prompt,
            'temperature': 0.1,
            'n_predict': 500,
            'stream': True,
        }

        async with httpx.AsyncClient(timeout=120) as client:
            async with client.stream('POST', f'http://{self.host}:{self.port}/completion', json=json_data) as response:
                full_response = ""
                async for chunk in response.aiter_bytes():
                    try:
                        data = json.loads(chunk.decode('utf-8')[6:])
                        if data['stop'] is False:
                            full_response += data['content']
                    except:
                        pass

        return full_response

    async def generate_sql_async(self, question: str, schema_description: str) -> str:
        """Generate SQL from natural language using LLM Runtime."""
        prompt = f"""
You are an expert SQL query generator for PostgreSQL databases.
Given the database schema below, generate a SQL query to answer the question.

{schema_description}

Question: {question}

IMPORTANT GUIDELINES:
1. Pay close attention to column semantics and meanings when choosing columns.
2. Distinguish between similar but functionally different columns (e.g., order_date vs. delivery_date vs. dispatch_date).
3. Use appropriate joins based on the relationships defined in the schema.
4. Ensure data types match when making comparisons.
5. Use table aliases for readability in complex queries.
6. For date/time operations, use appropriate PostgreSQL functions.

Format your response using triple backticks. Place the SQL query inside these backticks like this:
```sql
SELECT * FROM table WHERE condition;
```

Make sure the query inside the backticks is valid PostgreSQL syntax with no comments or additional text.
"""

        sql_query = await self.get_llama_response(prompt)
        return extract_sql_from_response(sql_query)

    def generate_sql(self, question: str, schema_description: str) -> str:
        """Synchronous wrapper for generate_sql_async."""
        return asyncio.run(self.generate_sql_async(question, schema_description))

    async def explain_results_async(self, question: str, sql_query: str, results: List[Dict[str, Any]], error: str = None) -> str:
        """Explain the results in natural language."""
        if error:
            prompt = f"""
Question: {question}

SQL Query: {sql_query}

Error: {error}

Please explain what went wrong with this query in simple terms and suggest how to fix it.
Be specific about any syntax errors or invalid references.
"""
        else:
            # Convert results to a string - limit to 10 results to manage context length
            results_str = json.dumps(results[:10], indent=2, default=str)

            prompt = f"""
Question: {question}

SQL Query: {sql_query}

Results: {results_str}

Provide a natural language explanation of these results that directly answers the original question.
When explaining the results:
1. Connect column names to their actual business meaning (e.g., "order_date refers to when the order was placed").
2. Distinguish between similar date columns if multiple are used (e.g., "delivery_date represents when the items were delivered to the customer, while dispatch_date shows when they left the warehouse").
3. Be clear about what each number or value represents in business terms.
4. Explain any aggregations or calculations performed.

Keep your explanation clear, concise, and focused on what the user actually asked.
If the results contain a lot of data, summarize the key points.
"""

        explanation = await self.get_llama_response(prompt)
        return explanation.strip()

    def explain_results(self, question: str, sql_query: str, results: List[Dict[str, Any]], error: str = None) -> str:
        """Synchronous wrapper for explain_results_async."""
        return asyncio.run(self.explain_results_async(question, sql_query, results, error))

def main():

    st.title("SQL Assistant powered by Power10 MMA")
    st.write("Ask questions about your PostgreSQL database in plain English.")

    # Check for session state initialization
    if 'connected' not in st.session_state:
        st.session_state['connected'] = False
    if 'query_history' not in st.session_state:
        st.session_state['query_history'] = []
    if 'llm_initialized' not in st.session_state:
        st.session_state['llm_initialized'] = False

    # Sidebar for LLM settings
    st.sidebar.header("Runtime API Settings")

    # LLM Runtime connection settings
    llama_host = st.sidebar.text_input("LLM Runtime API Host", "llama-service")
    llama_port = st.sidebar.text_input("LLM Runtime API Port", "8080")

    # Initialize LLM button
    if st.sidebar.button("Initialize LLM Runtime Interface"):
        with st.spinner("Initializing LLM Runtime interface..."):
            try:
                # Initialize the LLM Runtime interface
                llama_interface = LlamaInterface(
                    host=llama_host,
                    port=llama_port
                )

                # Store in session state
                st.session_state['llama_interface'] = llama_interface
                st.session_state['llm_initialized'] = True

                st.sidebar.success("LLM Runtime interface initialized successfully!")
            except Exception as e:
                st.sidebar.error(f"Error initializing LLM Runtime interface: {str(e)}")

    # Database connection inputs
    st.sidebar.header("Database Connection")
    db_host = st.sidebar.text_input("Host", "localhost")
    db_port = st.sidebar.text_input("Port", "5432")
    db_name = st.sidebar.text_input("Database Name")
    db_user = st.sidebar.text_input("Username")
    db_password = st.sidebar.text_input("Password", type="password")

    # Connect button
    if st.sidebar.button("Connect to Database"):
        if not all([db_name, db_user, db_password]):
            st.sidebar.error("Please provide all required database connection details.")
        elif not st.session_state['llm_initialized']:
            st.sidebar.error("Please initialize the LLM Runtime interface first.")
        else:
            with st.spinner("Connecting to database and analyzing schema..."):
                try:
                    # Initialize the database analyzer
                    db_analyzer = DatabaseAnalyzer(
                        dbname=db_name,
                        user=db_user,
                        password=db_password,
                        host=db_host,
                        port=db_port
                    )

                    # Try to connect
                    success, message = db_analyzer.connect()

                    if success:
                        st.sidebar.success(message)

                        # Analyze the schema
                        with st.spinner("Analyzing database schema and inferring column semantics..."):
                            schema_info = db_analyzer.analyze_schema()
                            schema_description = db_analyzer.generate_schema_description()
                            schema_for_llm = db_analyzer.generate_schema_for_llm()

                        # Store components in session state
                        st.session_state['db_analyzer'] = db_analyzer
                        st.session_state['schema_description'] = schema_description
                        st.session_state['schema_for_llm'] = schema_for_llm
                        st.session_state['connected'] = True

                        st.sidebar.success("Successfully connected and analyzed the database schema!")
                    else:
                        st.sidebar.error(message)
                except Exception as e:
                    st.sidebar.error(f"Error: {str(e)}")

    # Option to view database schema
    if st.session_state['connected']:
        with st.sidebar.expander("View Database Schema"):
            st.text(st.session_state['schema_description'])

    # Main area for question input
    st.header("Ask a Question")

    # Query input and submission
    question = st.text_area("Enter your question in plain English:", height=100,
                            placeholder="e.g., 'What were the top 5 selling products last month?' or 'How many users signed up in 2023?'")

    col1, col2 = st.columns([1, 5])
    with col1:
        ready_to_query = st.session_state['connected'] and st.session_state['llm_initialized']
        submit_button = st.button("Generate SQL", type="primary", disabled=not ready_to_query)
    with col2:
        if not st.session_state['llm_initialized']:
            st.info("Initialize the LLM Runtime interface first.")
        elif not st.session_state['connected']:
            st.info("Connect to a database to start asking questions.")

    # Process the question when submitted
    if submit_button and question:
        if not st.session_state['connected'] or not st.session_state['llm_initialized']:
            st.error("Please make sure the LLM Runtime interface is initialized and you're connected to a database.")
        else:
            # Create a container for the results
            results_container = st.container()

            with results_container:
                with st.spinner("Generating SQL query with LLM..."):
                    try:
                        # Generate SQL with LLM
                        raw_response = asyncio.run(st.session_state['llama_interface'].get_llama_response(
                            f"""
You are an expert SQL query generator for PostgreSQL databases.
Given the database schema below, generate a SQL query to answer the question.

{st.session_state['schema_for_llm']}

Question: {question}

IMPORTANT GUIDELINES:
1. Pay close attention to column semantics and meanings when choosing columns.
2. Distinguish between similar but functionally different columns (e.g., order_date vs. delivery_date vs. dispatch_date).
3. Use appropriate joins based on the relationships defined in the schema.
4. Ensure data types match when making comparisons.
5. Use table aliases for readability in complex queries.
6. For date/time operations, use appropriate PostgreSQL functions.

Format your response using triple backticks. Place the SQL query inside these backticks like this:
```sql
SELECT * FROM table WHERE condition;
```

Make sure the query inside the backticks is valid PostgreSQL syntax with no comments or additional text.
"""
                        ))

                        # Display the raw response in an expander for debugging
                        with st.expander("Raw LLM Response", expanded=False):
                            st.text(raw_response)

                        # Extract SQL query from the response
                        sql_query = extract_sql_from_response(raw_response)

                        # Display the extracted SQL
                        st.subheader("Generated SQL Query")
                        st.code(sql_query, language="sql")

                        # Execute the query
                        with st.spinner("Executing query..."):
                            try:
                                results, columns = st.session_state['db_analyzer'].execute_query(sql_query)

                                # Generate explanation
                                with st.spinner("Generating explanation with LLM..."):
                                    explanation = st.session_state['llama_interface'].explain_results(
                                        question,
                                        sql_query,
                                        results
                                    )

                                # Display explanation
                                st.subheader("Answer")
                                st.write(explanation)

                                # Display results as a table if available
                                if results and columns:
                                    with st.expander("View Detailed Results", expanded=True):
                                        st.subheader("Query Results")
                                        df = pd.DataFrame(results)
                                        st.dataframe(df)

                                        # Option to download results as CSV
                                        csv = df.to_csv(index=False)
                                        st.download_button(
                                            label="Download results as CSV",
                                            data=csv,
                                            file_name="query_results.csv",
                                            mime="text/csv"
                                        )

                                # Add to query history
                                st.session_state['query_history'].append({
                                    "question": question,
                                    "sql_query": sql_query,
                                    "results_count": len(results),
                                    "explanation": explanation,
                                    "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
                                })

                            except Exception as e:
                                st.error(f"Error executing the query: {str(e)}")

                                # Generate explanation for the error
                                with st.spinner("Analyzing the error with LLM..."):
                                    error_explanation = st.session_state['llama_interface'].explain_results(
                                        question,
                                        sql_query,
                                        [],
                                        error=str(e)
                                    )

                                st.subheader("Error Analysis")
                                st.write(error_explanation)

                                # Offer manual edit option
                                st.subheader("Fix the Query")
                                fixed_query = st.text_area("Edit the SQL query to fix the error:", value=sql_query, height=150)

                                if st.button("Execute Fixed Query"):
                                    with st.spinner("Executing fixed query..."):
                                        try:
                                            results, columns = st.session_state['db_analyzer'].execute_query(fixed_query)

                                            st.success("Query executed successfully!")

                                            # Display results
                                            if results and columns:
                                                st.subheader("Query Results")
                                                df = pd.DataFrame(results)
                                                st.dataframe(df)

                                                # Option to download results as CSV
                                                csv = df.to_csv(index=False)
                                                st.download_button(
                                                    label="Download results as CSV",
                                                    data=csv,
                                                    file_name="fixed_query_results.csv",
                                                    mime="text/csv"
                                                )
                                            else:
                                                st.info("Query executed successfully but returned no results.")

                                            # Add to query history
                                            st.session_state['query_history'].append({
                                                "question": f"FIXED: {question}",
                                                "sql_query": fixed_query,
                                                "results_count": len(results),
                                                "explanation": "Manually fixed query",
                                                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
                                            })

                                        except Exception as e:
                                            st.error(f"Error executing fixed query: {str(e)}")

                    except Exception as e:
                        st.error(f"Error generating SQL: {str(e)}")

    # Option to manually edit and execute a query
    st.header("Manual SQL Query")

    # Only allow editing if connected to a database
    if st.session_state.get('connected', False):
        manual_query = st.text_area("Enter SQL query manually:", height=100)

        if st.button("Execute Manual Query"):
            if not manual_query:
                st.error("Please enter a SQL query.")
            else:
                with st.spinner("Executing query..."):
                    try:
                        # Execute the query
                        results, columns = st.session_state['db_analyzer'].execute_query(manual_query)

                        # Display results
                        st.success("Query executed successfully!")

                        if results and columns:
                            st.subheader("Query Results")
                            df = pd.DataFrame(results)
                            st.dataframe(df)

                            # Option to download results as CSV
                            csv = df.to_csv(index=False)
                            st.download_button(
                                label="Download results as CSV",
                                data=csv,
                                file_name="manual_query_results.csv",
                                mime="text/csv"
                            )
                        else:
                            st.info("Query executed successfully but returned no results.")

                        # Add to query history
                        st.session_state['query_history'].append({
                            "question": "MANUAL QUERY",
                            "sql_query": manual_query,
                            "results_count": len(results) if results else 0,
                            "explanation": "Manually entered query",
                            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
                        })

                    except Exception as e:
                        st.error(f"Error executing the query: {str(e)}")
    else:
        st.info("Connect to a database to manually execute SQL queries.")

    # Display query history
    if st.session_state.get('connected', False) and st.session_state.get('query_history', []):
        st.header("Query History")

        # Create tabs for each history item
        history_items = list(reversed(st.session_state['query_history']))
        tab_labels = [f"Query {i+1}: {item['timestamp']}" for i, item in enumerate(history_items)]

        # Use tabs instead of nested expanders
        tabs = st.tabs(tab_labels)

        for i, (tab, item) in enumerate(zip(tabs, history_items)):
            with tab:
                st.markdown(f"**Question:** {item['question']}")
                st.markdown(f"**Time:** {item['timestamp']}")

                # Show SQL
                st.subheader("SQL Query")
                st.code(item['sql_query'], language="sql")

                # Show results info
                st.markdown(f"**Results:** {item['results_count']} rows returned")
                st.markdown(f"**Explanation:** {item['explanation']}")

                # Add a "Use this query" button
                if st.button(f"Use this query", key=f"use_query_{i}"):
                    st.session_state['reuse_query'] = item['sql_query']
                    st.experimental_rerun()

        # Check if there's a query to reuse
        if 'reuse_query' in st.session_state:
            # Pre-fill the manual query area
            st.session_state['manual_query'] = st.session_state['reuse_query']
            # Clear the reuse flag
            del st.session_state['reuse_query']

    # Display help information
    with st.expander("Help & Tips"):
        st.markdown("""
        ### How to Use This Tool

        1. **Initialize the LLM Runtime interface** by providing the host and port of your LLM Runtime API
        2. **Connect to your database** by entering your PostgreSQL credentials
        3. **Ask questions** about your data in plain English
        4. LLM Runtime will generate an SQL query, execute it, and explain the results

        ### Understanding Column Semantics

        The tool automatically infers the meaning of columns based on naming patterns.
        For example:
        - `created_at` typically represents when a record was created
        - `delivery_date` refers to when items were delivered to a customer
        - `dispatch_date` indicates when items were sent from the warehouse

        These semantic meanings help the LLM Runtime model choose the right columns
        when generating SQL queries from your questions.

        ### Effective Question Tips

        - **Be specific** about what you're looking for
        - **Use business terminology** that matches your data's purpose
        - For questions involving dates, clarify which date you mean (e.g., "Find orders shipped last month but not yet delivered")
        - **Specify time periods** for time-based queries
        - **Include aggregation terms** like "total," "average," or "count" when appropriate
        """)

    # Footer
    st.sidebar.markdown("---")
    st.sidebar.info("""
    **SQL Assistant powered by Power10 MMA**

    This app connects to your LLM Runtime API service to convert natural
    language questions into SQL queries for PostgreSQL databases.

    The app uses column semantic analysis to better understand
    your database structure and field meanings.

    Make sure your LLM Runtime API service is running and accessible at
    the host and port provided in the settings.
    """)

if __name__ == "__main__":
    main()
