// Maps the fully-qualified class names touched by the sample trace to their
// real source paths in test_code/java_project/java_project/src/main/java —
// the actual project this sample was extracted from.
export const CLASS_FILES: Record<string, string> = {
  "com.example.cpgtest.Main": "src/main/java/com/example/cpgtest/Main.java",
  "com.example.cpgtest.service.AccountService":
    "src/main/java/com/example/cpgtest/service/AccountService.java",
  "com.example.cpgtest.service.BankAccountService":
    "src/main/java/com/example/cpgtest/service/BankAccountService.java",
  "com.example.cpgtest.model.Account":
    "src/main/java/com/example/cpgtest/model/Account.java",
  "com.example.cpgtest.exception.InsufficientFundsException":
    "src/main/java/com/example/cpgtest/exception/InsufficientFundsException.java",
};
