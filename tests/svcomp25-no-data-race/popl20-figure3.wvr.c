// This file is part of the SV-Benchmarks collection of verification tasks:
// https://gitlab.com/sosy-lab/benchmarking/sv-benchmarks
//
// SPDX-FileCopyrightText: 2021 F. Schuessele <schuessf@informatik.uni-freiburg.de>
// SPDX-FileCopyrightText: 2021 D. Klumpp <klumpp@informatik.uni-freiburg.de>
//
// SPDX-License-Identifier: LicenseRef-BSD-3-Clause-Attribution-Vandikas

typedef unsigned long int pthread_t;

union pthread_attr_t
{
  char __size[36];
  long int __align;
};
typedef union pthread_attr_t pthread_attr_t;

extern void __assert_fail(const char *__assertion, const char *__file,
      unsigned int __line, const char *__function)
     __attribute__ ((__nothrow__ , __leaf__)) __attribute__ ((__noreturn__));
void reach_error() { __assert_fail("0", "popl20-figure3.wvr.c", 21, __extension__ __PRETTY_FUNCTION__); }
extern int pthread_create (pthread_t *__restrict __newthread,
      const pthread_attr_t *__restrict __attr,
      void *(*__start_routine) (void *),
      void *__restrict __arg) __attribute__ ((__nothrow__)) __attribute__ ((__nonnull__ (1, 3)));
extern int pthread_join (pthread_t __th, void **__thread_return);

extern unsigned int  __VERIFIER_nondet_uint(void);
extern _Bool __VERIFIER_nondet_bool(void);

extern void abort(void);
void assume_abort_if_not(int cond) {
  if(!cond) {abort();}
}

unsigned int N, c;
_Atomic unsigned int counter;

void* thread1(void* _argptr) {
  for (unsigned int i=0; i<N; i++) {
    counter += c;
  }

  return 0;
}

void* thread2(void* _argptr) {
  for (unsigned int i=0; i<N; i++) {
    assume_abort_if_not(counter > 0);
    counter -= c;
  }

  return 0;
}

int main() {
  pthread_t t1, t2;
  
  N = __VERIFIER_nondet_uint();
  c = __VERIFIER_nondet_uint();
  
  assume_abort_if_not(c > 0);
  
  // main method
  pthread_create(&t1, 0, thread1, 0);
  pthread_create(&t2, 0, thread2, 0);
  pthread_join(t1, 0);
  pthread_join(t2, 0);
  
  assume_abort_if_not(counter != 0);
  reach_error();

  return 0;
}