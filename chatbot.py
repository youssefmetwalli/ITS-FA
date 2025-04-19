import logging
import os
import google.generativeai as genai
# import PyPDF2
# from PyPDF2.errors import PdfReadError
# from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from langchain_community.vectorstores import FAISS
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder, HumanMessagePromptTemplate
from langchain_core.runnables import RunnablePassthrough, RunnableLambda
from langchain_core.output_parsers import StrOutputParser
from dotenv import load_dotenv

# Setup Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Constants 
PDF_FILE_PATH = "Automata Books/AutomataTheoryBook.pdf"
VECTOR_DB_PATH = "automata_vector_db"
VECTOR_STORE_FILE = "index.faiss"
PERSONA = "You are an AI assistant specialized in Automata Theory. Be kind, conversational, and helpful"

# Configure API key from environment variable
load_dotenv
genai.configure(api_key=os.environ.get("GOOGLE_API_KEY"))
model = genai.GenerativeModel('gemini-1.5-flash-002')


def load_and_chunk_pdf(pdf_path):
    """Loads a PDF, extracts text, and chunks it."""
    text = ""
    try:
        with open(pdf_path, "rb") as pdf_file:
            reader = PyPDF2.PdfReader(pdf_file)
            for page in reader.pages:
                text += page.extract_text() or ""

    except FileNotFoundError:
        logging.error(f"PDF not found at: {pdf_path}")
        return None
    except PdfReadError as e:
        logging.error(f"Error reading PDF: {e}")
        return None


    if not text:
        logging.warning("No text was extracted from the PDF.")
        return None

    text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=100)
    chunks = text_splitter.split_text(text)
    logging.info(f"Extracted and split the PDF into {len(chunks)} chunks.")
    return chunks


def create_embedding_index(chunks):
    """Creates a FAISS embedding index from the text chunks."""
    embeddings = GoogleGenerativeAIEmbeddings(model="models/embedding-001")
    vector_store = FAISS.from_texts(texts=chunks, embedding=embeddings)
    logging.info("Embedding index created.")
    return vector_store


def load_or_create_vector_db(overwrite=False):
    """Loads a vector database or creates a new one if it doesn't exist."""
    full_vector_path = os.path.join(VECTOR_DB_PATH,VECTOR_STORE_FILE)
    if os.path.exists(full_vector_path) and not overwrite:
        logging.info("Loading existing vector database.")
        embeddings = GoogleGenerativeAIEmbeddings(model="models/embedding-001")
        try:
             vector_store = FAISS.load_local(VECTOR_DB_PATH, embeddings, allow_dangerous_deserialization=True)
             return vector_store
        except Exception as e:
            logging.error(f"Error loading vector database: {e}")
            return None
    else:
        logging.info("Creating new vector database.")
        chunks = load_and_chunk_pdf(PDF_FILE_PATH)
        if chunks:
            vector_store = create_embedding_index(chunks)
            os.makedirs(VECTOR_DB_PATH, exist_ok=True)
            vector_store.save_local(VECTOR_DB_PATH)
            return vector_store
        else:
            return None


template = f"""{PERSONA}

Conversation so far:
{{chat_history}}

Relevant knowledge you can draw upon (do not mention its source) or refer to it at all when answering:
{{context}}

User's latest question:
{{question}}

You are an AI assistant specialized in Automata Theory. 
You respond concisely and clearly about Automata Theory topics. 
Only greet the user when they greet you first.
Answer in a friendly, conversational way, focusing on what the user has specifically asked for. Politelty decline answering any question unrelated to Automata Theory.
"""

prompt = ChatPromptTemplate.from_messages([
     ("system", template),
     HumanMessagePromptTemplate.from_template("{question}"),
 ])

# Initialize llm
llm = model

def create_chain(overwrite=False):
    vector_db = load_or_create_vector_db(overwrite)
    if vector_db is None:
        logging.error("Failed to load or create a vector database")
        return None

    chain = (
        {
            # We'll pass in the entire input as a dictionary,
            # which will have keys: {"chat_history": "...", "latest_question": "..."}
            "chat_history": RunnableLambda(lambda x: x["chat_history"]),
            "question": RunnableLambda(lambda x: x["question"]),
            "context": (
                RunnableLambda(
                    # Combine the full conversation and the user’s last question 
                    # so the retriever can see "DFA" references even if the new prompt is vague.
                    lambda x: vector_db.similarity_search(
                        f"{x['chat_history']}\n\nUser's latest question: {x['question']}",
                        k=4
                    )
                )
                # Join all retrieved chunks into a single text blob
                | (lambda docs: "\n".join([doc.page_content for doc in docs]))
            ),
        }
        # The ChatPromptTemplate references {chat_history}, {question}, {context}
        | prompt  
        # Generate the final LLM response
        | RunnableLambda(lambda x: llm.generate_content(contents=[m.content for m in x.to_messages()]).text)
        # Convert the response to a plain string
        | StrOutputParser()
    )

    return chain


def format_response(text):
   """Formats the response with markdown and adds extra lines for visual structure"""
   return f"\n{text}\n"


if __name__ == '__main__':
    chain = create_chain()
    if chain:
        while True:
            question = input("Ask a question about Automata Theory (or type 'exit' to quit): ")
            if question.lower() == 'exit':
                break
            logging.info(f"User Question: {question}")
            answer = chain.invoke(question)
            logging.info(f"Model Response: {answer}")
            formatted_answer = format_response(answer)
            print(f"Answer: {formatted_answer}")